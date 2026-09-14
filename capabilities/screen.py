"""Screenshot capability for Qubit with Screen Awareness and OCR Perception."""

import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from computer.screen import (
    screenshot as computer_screenshot,
    delete_screenshot,
    cleanup_screenshots,
)
from computer.screencast import ScreenCastCapture
from agent.screen_state import screen_state, ScreenState, ScreenObservation
from agent.screen_policy import screen_policy, ScreenAccessPolicy
from capabilities.windows import get_active_window
from perception import ocr_engine, create_screen_perception, ScreenPerception

logger = logging.getLogger(__name__)

_image_sender: Optional[Callable[[bytes], Awaitable[None]]] = None


def set_image_sender(sender: Optional[Callable[[bytes], Awaitable[None]]]) -> None:
    """Register the active callable used to transmit images to Gemini."""
    global _image_sender
    _image_sender = sender


def get_image_sender() -> Optional[Callable[[bytes], Awaitable[None]]]:
    """Retrieve the currently registered image sender callable."""
    return _image_sender


def get_screen_state() -> ScreenState:
    """Retrieve the global screen observation state manager."""
    return screen_state


def invalidate_screen_state(reason: str = "manual_invalidation") -> None:
    """Explicitly invalidate the current screen observation."""
    screen_state.invalidate(reason)


async def stop_screencast(reason: str = "manual_stop") -> None:
    """Explicitly stop any active ScreenCast session and release resources."""
    await screen_state.stop_screencast(reason=reason)


async def screenshot(
    force: bool = False,
    image_sender: Optional[Callable[[bytes], Awaitable[None]]] = None,
    **kwargs,
) -> str:
    """Capture a desktop screen frame and send it to the active Gemini Live session.

    ScreenCast Capture & Fallback:
        Uses an on-demand ScreenCast session over PipeWire while screen queries are active.
        Reuses the active ScreenCast stream to obtain the latest frame instantly
        without repeated shutter flashes or window prompts.
        Automatically stops the ScreenCast session after an idle timeout.
        If ScreenCast cannot start or fails, seamlessly falls back to the XDG
        Screenshot Portal.

    Screen Perception & OCR:
        Every captured frame is processed by both Gemini Live Vision and the local
        OCR perception engine for high-precision text reading.
    """
    sender = image_sender or _image_sender
    if sender is None:
        raise RuntimeError("No active Gemini Live session available to receive screenshot")

    # Detect if fresh capture requested via kwargs or parameter
    is_forced = bool(force or kwargs.get("fresh", False) or kwargs.get("force", False))
    requester = kwargs.get("requester")
    action = kwargs.get("action")
    user_query = kwargs.get("user_query")

    # Demand-driven access policy gate: only capture when explicitly justified
    allowed, policy_reason = screen_policy.evaluate_access(
        user_query=user_query,
        requester=requester,
        action=action,
        force=is_forced,
    )
    if not allowed:
        logger.info(f"Screen capture skipped by ScreenAccessPolicy: {policy_reason}")
        return (
            f"Screen capture skipped: Screen capture is strictly demand-driven and was not "
            f"requested by the user or required by the current action. ({policy_reason})"
        )

    # Identify currently active window
    try:
        active_window = get_active_window()
    except Exception as e:
        logger.warning(f"Could not retrieve active window: {e}")
        active_window = None

    image_bytes: Optional[bytes] = None
    capture_source = "screencast"
    temp_file_to_clean: Optional[Path] = None

    # 1. If ScreenCast is already active, grab the latest frame from the live stream
    if screen_state.is_screencast_active():
        session = screen_state.get_screencast_session()
        try:
            logger.info("Retrieving latest frame from active ScreenCast session...")
            image_bytes = await session.get_latest_frame()
            screen_state.touch_screencast()
            capture_source = "screencast_stream"
        except Exception as err:
            logger.warning(
                f"Failed to obtain frame from active ScreenCast session: {err}. "
                f"Stopping session and falling back."
            )
            await screen_state.stop_screencast(reason="screencast_frame_failed")
            session = None

    # 2. If ScreenCast was not active, check if existing observation is reusable
    if image_bytes is None and not is_forced:
        needs_capture, reason = screen_state.should_capture(
            force=False,
            current_window=active_window,
        )
        if not needs_capture:
            obs = screen_state.get_current_observation()
            window_desc = ""
            if obs and obs.window_info:
                caption = obs.window_info.get("caption") or ""
                app = obs.window_info.get("app") or ""
                window_desc = f" for '{caption}' ({app})"
            logger.info(f"Reusing existing screen observation{window_desc} (reason={reason})")

            perception_summary = ""
            if obs and obs.perception:
                perception_summary = f"\n\n{obs.perception.format_gemini_context()}"
            elif obs and obs.ocr_data:
                p = create_screen_perception(obs.window_info, obs.ocr_data, obs.timestamp)
                perception_summary = f"\n\n{p.format_gemini_context()}"

            return (
                f"Screen observation reused: Visual observation{window_desc} is current and already available in this session. "
                f"No new screenshot was needed.{perception_summary}"
            )

    # 3. If capture is needed and no frame obtained yet, start ScreenCast or fallback
    if image_bytes is None:
        try:
            logger.info("Starting on-demand ScreenCast session...")
            new_session = ScreenCastCapture()
            await new_session.start()
            screen_state.register_screencast_session(new_session)
            image_bytes = await new_session.get_latest_frame()
            capture_source = "screencast_new"
        except Exception as sc_err:
            logger.warning(
                f"ScreenCast session startup failed ({sc_err}). Falling back to XDG screenshot portal."
            )
            await screen_state.stop_screencast(reason="screencast_start_failed")
            try:
                temp_file_to_clean = await computer_screenshot()
                image_bytes = temp_file_to_clean.read_bytes()
                capture_source = "screenshot_fallback"
            except Exception:
                screen_state.invalidate("capture_failed")
                raise

    # 4. Process frame with OCR, Gemini Vision, and Screen Perception
    try:
        try:
            if temp_file_to_clean:
                ocr_data = ocr_engine.extract_from_file(temp_file_to_clean)
            else:
                ocr_data = ocr_engine.extract_from_bytes(image_bytes)
        except Exception as ocr_err:
            logger.warning(f"OCR extraction error (falling back to image only): {ocr_err}")
            ocr_data = {"full_text": "", "lines": [], "words": [], "error": str(ocr_err)}

        perception = create_screen_perception(active_window, ocr_data)

        # Transmit image to Gemini Live
        await sender(image_bytes)

        # Record observation state
        screen_state.record_observation(
            window_info=active_window,
            context={"source": capture_source, "size_bytes": len(image_bytes)},
            ocr_data=ocr_data,
            perception=perception,
        )

        window_desc = ""
        if active_window:
            caption = active_window.get("caption") or ""
            app = active_window.get("app") or ""
            window_desc = f" of '{caption}' ({app})"

        if "screencast" in capture_source:
            status_prefix = "Screen frame captured via ScreenCast and sent to Gemini successfully"
        else:
            status_prefix = "Screenshot captured and sent to Gemini successfully"

        perception_summary = f"\n\n{perception.format_gemini_context()}"
        return f"{status_prefix}{window_desc}.{perception_summary}"

    except Exception:
        screen_state.invalidate("transmission_failed")
        raise
    finally:
        if temp_file_to_clean:
            try:
                delete_screenshot(temp_file_to_clean)
            except Exception as e:
                logger.warning(f"Failed to delete temporary fallback screenshot {temp_file_to_clean}: {e}")

