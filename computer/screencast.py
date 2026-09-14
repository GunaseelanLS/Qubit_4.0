"""Low-level XDG Desktop ScreenCast Portal + PipeWire frame capture component for Qubit 4.0.

Provides on-demand ScreenCast session management and latest-frame retrieval
from PipeWire streams on Linux/Wayland without repeated screenshot flashes.
"""

import asyncio
import atexit
import io
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Optional, Tuple
import uuid

from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.aio import MessageBus
from PIL import Image

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # type: ignore[import-untyped,import-not-found]
    _GST_AVAILABLE = True
except (ImportError, ValueError):
    _GST_AVAILABLE = False
    Gst = None

logger = logging.getLogger(__name__)

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_INTERFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"

from security.screencast_permission import (
    load_restore_token,
    save_restore_token,
    clear_restore_token,
)

DEFAULT_START_TIMEOUT = float(os.getenv("QUBIT_SCREENCAST_START_TIMEOUT", 60.0))

_active_sessions = set()


def resolve_parent_window(parent_window: Optional[str] = None) -> str:
    """Determine the XDG Desktop Portal parent_window identifier.

    According to the XDG Desktop Portal specification:
    - X11: "x11:<xid_hex>"
    - Wayland: "wayland:<handle>" (exported via xdg_foreign protocol)
    - Headless, terminal CLI, or unknown: "" (empty string)

    Checks explicit parameter, environment variables, or WINDOWID.
    Returns standard "" if no valid identifier is present. Never invents fake IDs.
    """
    if parent_window is not None and parent_window.strip():
        return parent_window.strip()

    env_parent = os.getenv("QUBIT_SCREENCAST_PARENT_WINDOW") or os.getenv("XDG_PARENT_WINDOW")
    if env_parent and env_parent.strip():
        return env_parent.strip()

    # Check X11 / XWayland terminal WINDOWID
    window_id = os.getenv("WINDOWID")
    if window_id:
        try:
            wid_int = int(window_id.strip())
            return f"x11:{wid_int:x}"
        except ValueError:
            pass

    # Standard XDG portal specification value for non-GUI / terminal without window surface
    return ""


def _cleanup_all_active_sessions():
    """Emergency atexit handler to ensure no portal sessions or fds remain open."""
    for session in list(_active_sessions):
        try:
            session.stop_sync()
        except Exception:
            pass


atexit.register(_cleanup_all_active_sessions)


class ScreenCastCapture:
    """Low-level ScreenCast session and PipeWire stream controller."""

    def __init__(
        self,
        cursor_mode: int = 2,
        source_types: int = 1,
        start_timeout: Optional[float] = None,
        parent_window: Optional[str] = None,
    ):
        """Initialize ScreenCastCapture controller.

        Args:
            cursor_mode: 1=Hidden, 2=Embedded (default), 4=Metadata.
            source_types: 1=Monitor (default), 2=Window, 3=Both.
            start_timeout: Max seconds to wait for portal response (default 60.0 or env var).
            parent_window: Optional parent window identifier (e.g. "wayland:<handle>" or "x11:<xid>").
        """
        if not _GST_AVAILABLE:
            raise RuntimeError("GStreamer (gi.repository.Gst) is not available on this system.")

        Gst.init(None)

        self.cursor_mode = cursor_mode
        self.source_types = source_types
        self.start_timeout = start_timeout if start_timeout is not None else DEFAULT_START_TIMEOUT
        self.parent_window = parent_window

        self._bus: Optional[MessageBus] = None
        self._session_handle: Optional[str] = None
        self._pipewire_fd: Optional[int] = None
        self._node_id: Optional[int] = None

        self._pipeline: Optional[Any] = None
        self._lock = threading.Lock()
        self._latest_raw: Optional[Tuple[bytes, int, int, float]] = None
        self._latest_png_cache: Optional[Tuple[float, bytes]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._frame_event = asyncio.Event()

        self._is_active = False

    @property
    def is_active(self) -> bool:
        """Return whether the ScreenCast session and stream are currently active."""
        return self._is_active

    async def _wait_for_request(self, request_path: str, timeout: float = 60.0) -> Tuple[int, dict]:
        """Wait for the Response signal from an XDG portal request.

        Safely registers and unregisters D-Bus message handlers, preventing handler leaks.
        On timeout, closes the pending request on the portal to avoid orphaned dialogs.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def message_handler(message: Message):
            if (
                message.message_type == MessageType.SIGNAL
                and message.path == request_path
                and message.interface == REQUEST_INTERFACE
                and message.member == "Response"
            ):
                if not future.done():
                    future.set_result(message.body)

        self._bus.add_message_handler(message_handler)
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            response_code, response_data = result
            return response_code, response_data
        except asyncio.TimeoutError:
            logger.warning(
                f"[Portal:Request] Timeout waiting for portal response on {request_path} "
                f"after {timeout:.1f}s. Closing pending portal request."
            )
            if self._bus:
                try:
                    await self._bus.call(Message(
                        destination=PORTAL_SERVICE,
                        path=request_path,
                        interface=REQUEST_INTERFACE,
                        member="Close",
                        signature="",
                        body=[],
                    ))
                except Exception:
                    pass
            raise
        finally:
            if self._bus:
                try:
                    self._bus.remove_message_handler(message_handler)
                except Exception:
                    pass

    async def _cleanup_failed_attempt(self) -> None:
        """Clean up partial D-Bus/GStreamer resources after a failed restoration attempt."""
        if self._pipeline:
            try:
                self._pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._pipeline = None

        if self._pipewire_fd is not None and self._pipewire_fd >= 0:
            try:
                os.close(self._pipewire_fd)
            except OSError:
                pass
            self._pipewire_fd = None

        if self._bus and self._session_handle:
            try:
                await self._bus.call(Message(
                    destination=PORTAL_SERVICE,
                    path=self._session_handle,
                    interface=SESSION_INTERFACE,
                    member="Close",
                    signature="",
                    body=[],
                ))
            except Exception:
                pass
            self._session_handle = None

        if self._bus:
            try:
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None

        with self._lock:
            self._latest_raw = None
            self._latest_png_cache = None
        self._frame_event.clear()

    async def _setup_session(self, restore_token: Optional[str], timeout: float) -> bool:
        """Internal helper to execute portal D-Bus session setup and GStreamer pipeline start."""
        self._loop = asyncio.get_running_loop()
        self._frame_event = asyncio.Event()

        # Connect to SESSION bus with UNIX file descriptor passing enabled for OpenPipeWireRemote
        self._bus = await MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True).connect()

        # ------------------------------------------------------------------
        # 1. CreateSession
        # ------------------------------------------------------------------
        session_token = f"qubit_s_{uuid.uuid4().hex[:12]}"
        handle_token = f"qubit_r_{uuid.uuid4().hex[:12]}"
        logger.debug(
            f"[Portal:CreateSession] Requesting new session (session_token={session_token}, handle_token={handle_token})"
        )
        create_msg = Message(
            destination=PORTAL_SERVICE,
            path=PORTAL_PATH,
            interface=SCREENCAST_INTERFACE,
            member="CreateSession",
            signature="a{sv}",
            body=[{
                "session_handle_token": Variant("s", session_token),
                "handle_token": Variant("s", handle_token),
            }],
        )
        create_res = await self._bus.call(create_msg)
        if create_res.message_type == MessageType.ERROR:
            logger.error(f"[Portal:CreateSession] D-Bus call error: {create_res.body}")
            raise RuntimeError(f"CreateSession D-Bus error: {create_res.body}")

        req_path = create_res.body[0]
        code, data = await self._wait_for_request(req_path, timeout=timeout)
        if code != 0:
            logger.error(f"[Portal:CreateSession] Request failed with response code {code}: {data}")
            raise RuntimeError(f"CreateSession failed with code {code}: {data}")

        session_handle_variant = data.get("session_handle")
        if not session_handle_variant:
            logger.error(f"[Portal:CreateSession] No session_handle returned: {data}")
            raise RuntimeError(f"No session_handle in CreateSession response: {data}")
        self._session_handle = (
            session_handle_variant.value
            if hasattr(session_handle_variant, "value")
            else session_handle_variant
        )
        logger.info(f"[Portal:CreateSession] Session handle obtained: {self._session_handle}")

        # ------------------------------------------------------------------
        # 2. SelectSources
        # ------------------------------------------------------------------
        select_token = f"qubit_r_{uuid.uuid4().hex[:12]}"
        select_options = {
            "handle_token": Variant("s", select_token),
            "types": Variant("u", self.source_types),
            "multiple": Variant("b", False),
            "cursor_mode": Variant("u", self.cursor_mode),
            "persist_mode": Variant("u", 2),  # Persist until revoked
        }
        if restore_token:
            select_options["restore_token"] = Variant("s", restore_token)
            logger.debug(
                f"[Portal:SelectSources] Configuring sources with restore token "
                f"(types={self.source_types}, cursor_mode={self.cursor_mode}, persist_mode=2)"
            )
        else:
            logger.debug(
                f"[Portal:SelectSources] Configuring sources for fresh authorization "
                f"(types={self.source_types}, cursor_mode={self.cursor_mode}, persist_mode=2)"
            )

        select_msg = Message(
            destination=PORTAL_SERVICE,
            path=PORTAL_PATH,
            interface=SCREENCAST_INTERFACE,
            member="SelectSources",
            signature="oa{sv}",
            body=[self._session_handle, select_options],
        )
        select_res = await self._bus.call(select_msg)
        if select_res.message_type == MessageType.ERROR:
            logger.error(f"[Portal:SelectSources] D-Bus call error: {select_res.body}")
            raise RuntimeError(f"SelectSources D-Bus error: {select_res.body}")

        code, data = await self._wait_for_request(select_res.body[0], timeout=timeout)
        if code != 0:
            logger.error(f"[Portal:SelectSources] Request failed with response code {code}: {data}")
            raise RuntimeError(f"SelectSources failed with code {code}: {data}")
        logger.info("[Portal:SelectSources] Sources selected successfully.")

        # ------------------------------------------------------------------
        # 3. Start Session
        # ------------------------------------------------------------------
        start_token = f"qubit_r_{uuid.uuid4().hex[:12]}"
        parent_win = resolve_parent_window(self.parent_window)

        start_options: dict = {
            "handle_token": Variant("s", start_token),
        }

        # Propagate Wayland activation token if available in environment
        activation_token = os.getenv("XDG_ACTIVATION_TOKEN")
        if activation_token and activation_token.strip():
            start_options["activation_token"] = Variant("s", activation_token.strip())
            logger.debug("[Portal:Start] Passing XDG_ACTIVATION_TOKEN in Start options.")

        logger.info(
            f"[Portal:Start] Requesting ScreenCast start (parent_window='{parent_win}', "
            f"timeout={timeout:.1f}s)..."
        )
        start_msg = Message(
            destination=PORTAL_SERVICE,
            path=PORTAL_PATH,
            interface=SCREENCAST_INTERFACE,
            member="Start",
            signature="osa{sv}",
            body=[self._session_handle, parent_win, start_options],
        )
        start_res = await self._bus.call(start_msg)
        if start_res.message_type == MessageType.ERROR:
            logger.error(f"[Portal:Start] D-Bus call error: {start_res.body}")
            raise RuntimeError(f"Start D-Bus error: {start_res.body}")

        code, data = await self._wait_for_request(start_res.body[0], timeout=timeout)
        if code != 0:
            error_desc = {
                1: "User cancelled or denied authorization",
                2: "Request dismissed or portal closed",
            }.get(code, f"Portal error code {code}")
            logger.warning(f"[Portal:Start] Authorization response code {code} ({error_desc}): {data}")
            raise RuntimeError(f"Start failed with code {code} ({error_desc}): {data}")

        # Extract streams
        streams = data.get("streams", [])
        if hasattr(streams, "value"):
            streams = streams.value
        if not streams:
            logger.error(f"[Portal:Start] No streams returned by ScreenCast portal: {data}")
            raise RuntimeError(f"No streams returned by ScreenCast portal: {data}")

        first_stream = streams[0]
        node_id = first_stream[0]
        if hasattr(node_id, "value"):
            node_id = node_id.value
        self._node_id = int(node_id)
        logger.info(f"[Portal:Start] ScreenCast authorized. PipeWire node ID: {self._node_id}")

        # Save new or refreshed restore token
        restore_token_var = data.get("restore_token")
        if restore_token_var:
            val = restore_token_var.value if hasattr(restore_token_var, "value") else restore_token_var
            if isinstance(val, str) and val.strip():
                save_restore_token(val.strip())
                logger.info("[Portal:Start] Persistent restore token stored successfully.")

        # ------------------------------------------------------------------
        # 4. OpenPipeWireRemote (get PipeWire fd)
        # ------------------------------------------------------------------
        try:
            logger.debug(f"[Portal:OpenPipeWireRemote] Requesting PipeWire remote fd for {self._session_handle}...")
            open_remote_msg = Message(
                destination=PORTAL_SERVICE,
                path=PORTAL_PATH,
                interface=SCREENCAST_INTERFACE,
                member="OpenPipeWireRemote",
                signature="oa{sv}",
                body=[self._session_handle, {}],
            )
            open_remote_res = await self._bus.call(open_remote_msg)
            if open_remote_res.message_type != MessageType.ERROR:
                if open_remote_res.unix_fds:
                    # In D-Bus protocol, body[0] contains the index into message.unix_fds
                    fd_idx = 0
                    if open_remote_res.body:
                        val = open_remote_res.body[0]
                        fd_idx = int(val.value if hasattr(val, "value") else val)
                    if 0 <= fd_idx < len(open_remote_res.unix_fds):
                        self._pipewire_fd = open_remote_res.unix_fds[fd_idx]
                    else:
                        self._pipewire_fd = open_remote_res.unix_fds[0]
                    logger.info(f"[Portal:OpenPipeWireRemote] Obtained PipeWire remote fd: {self._pipewire_fd}")
                elif open_remote_res.body:
                    raw_fd = open_remote_res.body[0]
                    val = int(raw_fd.value if hasattr(raw_fd, "value") else raw_fd)
                    if val > 2:  # Exclude stdin/stdout/stderr
                        self._pipewire_fd = val
                        logger.info(f"[Portal:OpenPipeWireRemote] Obtained PipeWire remote fd from body: {self._pipewire_fd}")
        except Exception as e:
            logger.debug(f"[Portal:OpenPipeWireRemote] Notice (will use standard path): {e}")

        # 5. Build GStreamer Pipeline
        self._start_gstreamer_pipeline()

        # 6. Wait for the initial frame to arrive
        logger.debug("Waiting for first ScreenCast frame...")
        await asyncio.wait_for(self._frame_event.wait(), timeout=6.0)

        self._is_active = True
        _active_sessions.add(self)
        logger.info("ScreenCast stream established successfully. Ready for frame capture.")
        return True

    async def start(self, timeout: Optional[float] = None) -> bool:
        """Create and start the ScreenCast session, connecting PipeWire to GStreamer.

        Restores persistent authorization if a valid restore_token is stored;
        otherwise requests persistent authorization from the user and saves the token.
        If restoration fails (e.g. invalid/revoked token), automatically falls back
        to requesting fresh authorization and saves the new token.

        Returns:
            bool: True if started successfully.
        """
        if self._is_active:
            return True

        effective_timeout = timeout if timeout is not None else self.start_timeout

        saved_token = load_restore_token()
        if saved_token:
            logger.info("Stored ScreenCast restore token found. Attempting session restoration...")
            try:
                await self._setup_session(restore_token=saved_token, timeout=effective_timeout)
                logger.info("ScreenCast session successfully restored without user prompt.")
                return True
            except Exception as restore_err:
                logger.warning(
                    f"ScreenCast session restoration with stored token failed ({restore_err}). "
                    f"Clearing stored token and requesting fresh authorization..."
                )
                clear_restore_token()
                await self._cleanup_failed_attempt()

        # Either no token existed, or restoration failed: request fresh authorization
        logger.info("Requesting ScreenCast authorization with persistence...")
        try:
            await self._setup_session(restore_token=None, timeout=effective_timeout)
            logger.info("ScreenCast session authorized and started successfully.")
            return True
        except Exception as e:
            logger.warning(f"Failed to start ScreenCast session: {e}")
            await self.stop()
            raise

    def _start_gstreamer_pipeline(self) -> None:
        """Construct and run the GStreamer pipeline receiving frames into an appsink."""
        src = Gst.ElementFactory.make("pipewiresrc", "src")
        if not src:
            raise RuntimeError("Failed to create GStreamer pipewiresrc element.")

        src.set_property("path", str(self._node_id))
        src.set_property("client-name", "Qubit ScreenCast")
        src.set_property("keepalive-time", 1000)

        if self._pipewire_fd is not None and self._pipewire_fd >= 0:
            try:
                src.set_property("fd", self._pipewire_fd)
            except Exception as fd_err:
                logger.debug(f"Could not set fd on pipewiresrc: {fd_err}")

        conv = Gst.ElementFactory.make("videoconvert", "conv")
        sink = Gst.ElementFactory.make("appsink", "sink")
        sink.set_property("emit-signals", True)
        sink.set_property("max-buffers", 1)
        sink.set_property("drop", True)
        caps = Gst.Caps.from_string("video/x-raw,format=RGB")
        sink.set_property("caps", caps)

        pipe = Gst.Pipeline.new(f"screencast_pipe_{uuid.uuid4().hex[:6]}")
        pipe.add(src)
        pipe.add(conv)
        pipe.add(sink)
        src.link(conv)
        conv.link(sink)

        sink.connect("new-sample", self._on_new_sample)

        ret = pipe.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer pipeline failed to enter PLAYING state.")

        self._pipeline = pipe

    def _on_new_sample(self, sink) -> Any:
        """GStreamer appsink callback triggered when a new frame is ready."""
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        if not caps:
            return Gst.FlowReturn.OK

        s = caps.get_structure(0)
        width = s.get_value("width")
        height = s.get_value("height")

        success, map_info = buf.map(Gst.MapFlags.READ)
        if success:
            raw_bytes = bytes(map_info.data)
            buf.unmap(map_info)
            now = time.time()
            with self._lock:
                self._latest_raw = (raw_bytes, width, height, now)
                # Invalidate PNG cache since we have a newer raw frame
                self._latest_png_cache = None

            if not self._frame_event.is_set():
                # Set event thread-safely across asyncio loops from GStreamer thread
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(self._frame_event.set)
                else:
                    try:
                        self._frame_event.set()
                    except RuntimeError:
                        pass

        return Gst.FlowReturn.OK

    async def get_latest_frame(self, timeout: float = 3.0) -> bytes:
        """Retrieve the latest screen frame as encoded PNG bytes.

        Returns:
            bytes: PNG formatted screen image bytes.
        """
        if not self._is_active:
            raise RuntimeError("ScreenCast session is not active.")

        # Ensure at least one frame is available
        if self._latest_raw is None:
            await asyncio.wait_for(self._frame_event.wait(), timeout=timeout)

        with self._lock:
            if self._latest_raw is None:
                raise RuntimeError("No frame available from ScreenCast stream.")

            # Return cached PNG if raw frame has not updated
            raw_bytes, width, height, ts = self._latest_raw
            if self._latest_png_cache is not None and self._latest_png_cache[0] == ts:
                return self._latest_png_cache[1]

        # Convert raw RGB bytes to PNG
        image = Image.frombytes("RGB", (width, height), raw_bytes)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with self._lock:
            self._latest_png_cache = (ts, png_bytes)

        return png_bytes

    async def stop(self) -> None:
        """Stop the ScreenCast session, GStreamer pipeline, and release all resources."""
        self._is_active = False
        _active_sessions.discard(self)

        # 1. Stop GStreamer Pipeline
        if self._pipeline:
            try:
                self._pipeline.set_state(Gst.State.NULL)
            except Exception as e:
                logger.debug(f"Error stopping GStreamer pipeline: {e}")
            self._pipeline = None

        # 2. Close PipeWire file descriptor
        if self._pipewire_fd is not None and self._pipewire_fd >= 0:
            try:
                os.close(self._pipewire_fd)
            except OSError:
                pass
            self._pipewire_fd = None

        # 3. Close Portal Session
        if self._bus and self._session_handle:
            try:
                await self._bus.call(Message(
                    destination=PORTAL_SERVICE,
                    path=self._session_handle,
                    interface=SESSION_INTERFACE,
                    member="Close",
                    signature="",
                    body=[],
                ))
            except Exception as e:
                logger.debug(f"Error closing portal session: {e}")
            self._session_handle = None

        # 4. Disconnect D-Bus
        if self._bus:
            try:
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None

        with self._lock:
            self._latest_raw = None
            self._latest_png_cache = None
        self._frame_event.clear()

        logger.info("ScreenCast session stopped and resources released.")

    def stop_sync(self) -> None:
        """Synchronous cleanup for atexit handler."""
        self._is_active = False
        _active_sessions.discard(self)

        if self._pipeline:
            try:
                self._pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._pipeline = None

        if self._pipewire_fd is not None and self._pipewire_fd >= 0:
            try:
                os.close(self._pipewire_fd)
            except OSError:
                pass
            self._pipewire_fd = None

        if self._bus:
            try:
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None
