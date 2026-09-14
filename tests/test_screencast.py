"""Unit and integration tests for Qubit 4.0 on-demand ScreenCast system.

Covers all 7 required test cases:
TEST 1: Qubit idle -> ScreenCast OFF
TEST 2: Screen question -> ScreenCast starts, frame obtained, Gemini receives frame, OCR processes it
TEST 3: Consecutive screen question -> Active ScreenCast session reused, latest frame used
TEST 4: Screen changes / scrolling -> Latest frame reflects updated content
TEST 5: Idle timeout -> ScreenCast stops, resources released
TEST 6: ScreenCast failure -> Fallback to XDG screenshot portal
TEST 7: Session termination -> Clean resource cleanup
"""

import asyncio
import io
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to sys.path so tests can be run directly from tests/ or IDEs
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image

from agent.screen_state import ScreenState, screen_state
from capabilities.screen import (
    screenshot,
    set_image_sender,
    stop_screencast,
)
from computer.screencast import (
    ScreenCastCapture,
    resolve_parent_window,
    DEFAULT_START_TIMEOUT,
)
from security import (
    load_restore_token,
    save_restore_token,
    clear_restore_token,
)


def _create_test_png(color=(255, 0, 0), text_marker: str = "TEST_FRAME") -> bytes:
    """Helper to create distinct test PNG images."""
    img = Image.new("RGB", (320, 240), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class MockScreenCastSession:
    """Mock ScreenCastCapture session simulating PipeWire frame stream."""

    def __init__(self, initial_frame: Optional[bytes] = None):
        self.is_active = False
        self.start_call_count = 0
        self.stop_call_count = 0
        self._current_frame = initial_frame or _create_test_png((10, 20, 30))

    def update_frame(self, new_frame_bytes: bytes):
        """Simulate a new frame arriving from PipeWire stream."""
        self._current_frame = new_frame_bytes

    async def start(self, timeout=None) -> bool:
        self.start_call_count += 1
        self.is_active = True
        return True

    async def get_latest_frame(self, timeout=3.0) -> bytes:
        if not self.is_active:
            raise RuntimeError("ScreenCast session is not active.")
        return self._current_frame

    async def stop(self) -> None:
        self.stop_call_count += 1
        self.is_active = False


class TestScreenCastUnitAndLifecycle(unittest.IsolatedAsyncioTestCase):
    """Unit tests for ScreenCastCapture and ScreenState lifecycle."""

    async def asyncSetUp(self):
        screen_state.clear()
        await screen_state.stop_screencast()

    async def asyncTearDown(self):
        screen_state.clear()
        await screen_state.stop_screencast()

    async def test_case_1_qubit_idle_screencast_off(self):
        """TEST 1: Qubit is idle -> No ScreenCast session exists."""
        self.assertFalse(screen_state.is_screencast_active())
        self.assertIsNone(screen_state.get_screencast_session())

    async def test_case_5_idle_timeout_stops_screencast(self):
        """TEST 5: No screen-related activity for timeout -> ScreenCast stops and releases resources."""
        mock_session = MockScreenCastSession()
        await mock_session.start()

        # Set a very short timeout for test (0.15s)
        test_state = ScreenState(screencast_idle_timeout=0.15)
        test_state.register_screencast_session(mock_session)

        self.assertTrue(test_state.is_screencast_active())
        self.assertTrue(mock_session.is_active)

        # Wait for idle timeout to expire
        await asyncio.sleep(0.25)

        self.assertFalse(test_state.is_screencast_active())
        self.assertFalse(mock_session.is_active)
        self.assertEqual(mock_session.stop_call_count, 1)

    async def test_touch_screencast_resets_idle_timer(self):
        """Touching the screencast extends session life and prevents early timeout."""
        mock_session = MockScreenCastSession()
        await mock_session.start()

        test_state = ScreenState(screencast_idle_timeout=0.2)
        test_state.register_screencast_session(mock_session)

        # Touch at 0.1s to extend
        await asyncio.sleep(0.1)
        test_state.touch_screencast()

        # At 0.2s total, session should still be alive because of touch
        await asyncio.sleep(0.05)
        self.assertTrue(test_state.is_screencast_active())

        # Wait remaining time to let it timeout
        await asyncio.sleep(0.2)
        self.assertFalse(test_state.is_screencast_active())


class TestScreenCastCapabilityIntegration(unittest.IsolatedAsyncioTestCase):
    """End-to-end integration tests for ScreenCast capture flow with Gemini and OCR."""

    async def asyncSetUp(self):
        screen_state.clear()
        await screen_state.stop_screencast()

        self.sent_frames = []

        async def mock_sender(data: bytes):
            self.sent_frames.append(data)

        self.mock_sender = mock_sender
        set_image_sender(mock_sender)

        self.mock_session = MockScreenCastSession()

    async def asyncTearDown(self):
        set_image_sender(None)
        await screen_state.stop_screencast()
        screen_state.clear()

    @patch("capabilities.screen.get_active_window")
    async def test_case_2_and_3_on_demand_start_and_reuse(self, mock_active_win):
        """TEST 2 & TEST 3:

        TEST 2: First question -> ScreenCast starts, frame obtained, Gemini receives it.
        TEST 3: Follow-up question -> Existing ScreenCast session reused without creating new session.
        """
        mock_active_win.return_value = {"app": "vscode", "caption": "main.py", "uuid": "win-1"}
        frame1 = _create_test_png((100, 150, 200))
        self.mock_session.update_frame(frame1)

        with patch("capabilities.screen.ScreenCastCapture", return_value=self.mock_session):
            # TEST 2: Initial screen request
            self.assertFalse(screen_state.is_screencast_active())
            result1 = await screenshot()

            self.assertIn("Screen frame captured via ScreenCast and sent to Gemini successfully", result1)
            self.assertEqual(len(self.sent_frames), 1)
            self.assertEqual(self.sent_frames[0], frame1)
            self.assertEqual(self.mock_session.start_call_count, 1)
            self.assertTrue(screen_state.is_screencast_active())

            # TEST 3: Consecutive screen request (ScreenCast session active)
            result2 = await screenshot()

            self.assertIn("Screen frame captured via ScreenCast and sent to Gemini successfully", result2)
            self.assertEqual(len(self.sent_frames), 2)
            # Reused existing session: start_call_count must remain 1!
            self.assertEqual(self.mock_session.start_call_count, 1)
            self.assertEqual(self.mock_session.stop_call_count, 0)
            self.assertTrue(screen_state.is_screencast_active())

    @patch("capabilities.screen.get_active_window")
    async def test_case_4_screen_change_scroll_reflects_latest_frame(self, mock_active_win):
        """TEST 4: User scrolls / changes visible screen -> Latest frame reflects new content."""
        mock_active_win.return_value = {"app": "browser", "caption": "Documentation", "uuid": "win-2"}
        initial_frame = _create_test_png((0, 0, 255))
        scrolled_frame = _create_test_png((0, 255, 0))

        self.mock_session.update_frame(initial_frame)

        with patch("capabilities.screen.ScreenCastCapture", return_value=self.mock_session):
            # 1. Ask initial question
            await screenshot()
            self.assertEqual(self.sent_frames[-1], initial_frame)

            # 2. User scrolls (simulated by updating stream frame in mock session)
            self.mock_session.update_frame(scrolled_frame)

            # 3. User asks another question
            await screenshot()

            # The latest frame sent to Gemini MUST be the updated scrolled frame
            self.assertEqual(self.sent_frames[-1], scrolled_frame)
            self.assertNotEqual(self.sent_frames[-1], initial_frame)

    @patch("capabilities.screen.get_active_window")
    @patch("capabilities.screen.computer_screenshot")
    @patch("capabilities.screen.delete_screenshot")
    async def test_case_6_screencast_failure_falls_back_to_screenshot(
        self, mock_delete, mock_comp_screenshot, mock_active_win
    ):
        """TEST 6: ScreenCast fails -> Existing XDG Screenshot mechanism is used as fallback."""
        mock_active_win.return_value = {"app": "terminal", "caption": "bash", "uuid": "win-3"}

        # Create temporary fallback screenshot file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            fallback_png_bytes = _create_test_png((255, 255, 0))
            tf.write(fallback_png_bytes)
            tf_path = Path(tf.name)

        mock_comp_screenshot.return_value = tf_path

        # Simulate ScreenCast failure on start
        failing_session = MagicMock()
        failing_session.start = AsyncMock(side_effect=RuntimeError("Portal denied ScreenCast"))
        failing_session.stop = AsyncMock()

        with patch("capabilities.screen.ScreenCastCapture", return_value=failing_session):
            result = await screenshot()

            # Check that fallback was used
            self.assertIn("Screenshot captured and sent to Gemini successfully", result)
            self.assertEqual(len(self.sent_frames), 1)
            self.assertEqual(self.sent_frames[0], fallback_png_bytes)
            self.assertTrue(mock_comp_screenshot.called)
            self.assertTrue(mock_delete.called)
            self.assertFalse(screen_state.is_screencast_active())

        if tf_path.exists():
            tf_path.unlink()

    @patch("capabilities.screen.get_active_window")
    async def test_case_7_session_termination_cleans_up_resources(self, mock_active_win):
        """TEST 7: Qubit / Gemini session terminates while ScreenCast active -> Clean resource cleanup."""
        mock_active_win.return_value = {"app": "editor", "caption": "test.py", "uuid": "win-4"}
        self.mock_session.update_frame(_create_test_png((50, 50, 50)))

        with patch("capabilities.screen.ScreenCastCapture", return_value=self.mock_session):
            await screenshot()
            self.assertTrue(screen_state.is_screencast_active())

            # Simulate session termination
            await stop_screencast(reason="gemini_session_ended")

            self.assertFalse(screen_state.is_screencast_active())
            self.assertEqual(self.mock_session.stop_call_count, 1)

    @patch("capabilities.screen.get_active_window")
    async def test_ocr_processes_screencast_frame(self, mock_active_win):
        """Verify that OCR pipeline processes the ScreenCast frame seamlessly."""
        mock_active_win.return_value = {"app": "editor", "caption": "code.py", "uuid": "win-5"}
        frame_bytes = _create_test_png((200, 200, 200))
        self.mock_session.update_frame(frame_bytes)

        with patch("capabilities.screen.ScreenCastCapture", return_value=self.mock_session):
            with patch("capabilities.screen.ocr_engine.extract_from_bytes") as mock_ocr:
                mock_ocr.return_value = {
                    "full_text": "def test_hello(): pass",
                    "lines": [{"text": "def test_hello(): pass", "bbox": [0, 0, 100, 20], "confidence": 95.0}],
                    "words": [{"text": "def", "bbox": [0, 0, 20, 20], "confidence": 95.0}],
                }

                result = await screenshot()

                # Both OCR and Gemini received the frame
                mock_ocr.assert_called_once_with(frame_bytes)
                self.assertIn("[Screen Text Content (OCR)]", result)
                self.assertIn("def test_hello(): pass", result)


class TestPersistentScreenCastPermission(unittest.IsolatedAsyncioTestCase):
    """Tests for persistent ScreenCast permission, restore token storage, and fallback."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_file = Path(self.temp_dir.name) / "test_restore_token"
        os.environ["QUBIT_SCREENCAST_TOKEN_FILE"] = str(self.token_file)

    def tearDown(self):
        os.environ.pop("QUBIT_SCREENCAST_TOKEN_FILE", None)
        self.temp_dir.cleanup()

    def test_token_storage_crud(self):
        """Verify saving, loading, and clearing tokens with secure permissions."""
        self.assertIsNone(load_restore_token())

        save_restore_token("tok_alpha_123")
        self.assertEqual(load_restore_token(), "tok_alpha_123")
        self.assertTrue(self.token_file.exists())

        # Verify secure permissions (0o600)
        mode = os.stat(self.token_file).st_mode & 0o777
        self.assertEqual(mode, 0o600)

        # Overwrite / replace token
        save_restore_token("tok_beta_456")
        self.assertEqual(load_restore_token(), "tok_beta_456")

        # Clear token
        clear_restore_token()
        self.assertIsNone(load_restore_token())
        self.assertFalse(self.token_file.exists())

    async def test_first_run_authorization_saves_token(self):
        """On first authorization (no token stored), requests fresh authorization and saves token."""
        capture = ScreenCastCapture()
        setup_calls = []

        async def mock_setup(restore_token=None, timeout=None):
            setup_calls.append(restore_token)
            # Portal returns a new restore token
            save_restore_token("portal_token_new_1")
            capture._is_active = True
            return True

        capture._setup_session = mock_setup

        success = await capture.start()
        self.assertTrue(success)
        self.assertEqual(setup_calls, [None])
        self.assertEqual(load_restore_token(), "portal_token_new_1")

    async def test_subsequent_launch_restores_without_prompt(self):
        """When restore token is stored, subsequent launch passes restore_token."""
        save_restore_token("existing_token_abc")

        capture = ScreenCastCapture()
        setup_calls = []

        async def mock_setup(restore_token=None, timeout=None):
            setup_calls.append(restore_token)
            capture._is_active = True
            return True

        capture._setup_session = mock_setup

        success = await capture.start()
        self.assertTrue(success)
        # Attempted restore with existing token without fallback prompt
        self.assertEqual(setup_calls, ["existing_token_abc"])
        self.assertEqual(load_restore_token(), "existing_token_abc")

    async def test_restoration_token_update(self):
        """When the portal provides a rotated restore token on restoration, replace old token."""
        save_restore_token("old_token_111")

        capture = ScreenCastCapture()

        async def mock_setup(restore_token=None, timeout=None):
            # Portal rotated the token
            save_restore_token("rotated_token_222")
            capture._is_active = True
            return True

        capture._setup_session = mock_setup

        await capture.start()
        self.assertEqual(load_restore_token(), "rotated_token_222")

    async def test_invalid_or_revoked_token_fallback(self):
        """If restoration fails with invalid/revoked token, clear token and request fresh authorization."""
        save_restore_token("revoked_token_999")

        capture = ScreenCastCapture()
        setup_calls = []

        async def mock_setup(restore_token=None, timeout=None):
            setup_calls.append(restore_token)
            if restore_token == "revoked_token_999":
                raise RuntimeError("Portal error: invalid or revoked restore token")
            # Fresh authorization succeeds
            save_restore_token("fresh_token_after_fallback")
            capture._is_active = True
            return True

        capture._setup_session = mock_setup
        capture._cleanup_failed_attempt = AsyncMock()

        success = await capture.start()
        self.assertTrue(success)
        # Attempted with revoked token first, then fell back to None (prompting fresh authorization)
        self.assertEqual(setup_calls, ["revoked_token_999", None])
        self.assertEqual(load_restore_token(), "fresh_token_after_fallback")
        self.assertTrue(capture._cleanup_failed_attempt.called)

    async def test_both_restoration_and_fresh_auth_fail_raises(self):
        """If both restoration and fresh authorization fail, raise error for screenshot fallback."""
        save_restore_token("bad_token")

        capture = ScreenCastCapture()

        async def mock_setup(restore_token=None, timeout=None):
            raise RuntimeError("Portal completely unavailable")

        capture._setup_session = mock_setup
        capture._cleanup_failed_attempt = AsyncMock()
        capture.stop = AsyncMock()

        with self.assertRaises(RuntimeError):
            await capture.start()

        # Token was cleared when restoration failed
        self.assertIsNone(load_restore_token())
        self.assertTrue(capture.stop.called)


class TestScreenCastPortalAuthorization(unittest.IsolatedAsyncioTestCase):
    """Tests for GNOME Wayland ScreenCast portal authorization and parent context handling."""

    def test_default_timeout_is_60s(self):
        """ScreenCast default start timeout is 60.0s and configurable via environment variable."""
        self.assertEqual(DEFAULT_START_TIMEOUT, 60.0)
        capture = ScreenCastCapture()
        self.assertEqual(capture.start_timeout, 60.0)

        # Custom override in constructor
        capture_custom = ScreenCastCapture(start_timeout=15.0)
        self.assertEqual(capture_custom.start_timeout, 15.0)

    def test_resolve_parent_window_precedence(self):
        """Verify parent_window resolution respects precedence and never invents fake IDs."""
        # 1. Explicit argument has highest priority
        self.assertEqual(resolve_parent_window("wayland:custom-surface-1"), "wayland:custom-surface-1")

        # 2. QUBIT_SCREENCAST_PARENT_WINDOW env var
        with patch.dict(os.environ, {"QUBIT_SCREENCAST_PARENT_WINDOW": "wayland:env-handle"}):
            self.assertEqual(resolve_parent_window(), "wayland:env-handle")

        # 3. XDG_PARENT_WINDOW env var
        with patch.dict(os.environ, {"XDG_PARENT_WINDOW": "wayland:xdg-handle"}, clear=True):
            self.assertEqual(resolve_parent_window(), "wayland:xdg-handle")

        # 4. WINDOWID env var (from X11 / XWayland terminal) converted to hex format
        with patch.dict(os.environ, {"WINDOWID": "123456"}, clear=True):
            self.assertEqual(resolve_parent_window(), "x11:1e240")

        # 5. Fallback is standard empty string "" according to XDG Desktop Portal specification
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_parent_window(), "")
            self.assertEqual(resolve_parent_window("   "), "")

    @patch("computer.screencast.MessageBus")
    async def test_setup_session_passes_parent_window_and_activation_token(self, mock_bus_cls):
        """Verify Start() passes resolved parent_window and activation_token if present."""
        mock_bus = AsyncMock()
        mock_bus.disconnect = MagicMock()
        mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)

        # Mock bus.call responses for CreateSession, SelectSources, Start, OpenPipeWireRemote
        create_res = MagicMock()
        create_res.message_type = 1
        create_res.body = ["/org/freedesktop/portal/desktop/request/1/create_1"]

        select_res = MagicMock()
        select_res.message_type = 1
        select_res.body = ["/org/freedesktop/portal/desktop/request/1/select_1"]

        start_res = MagicMock()
        start_res.message_type = 1
        start_res.body = ["/org/freedesktop/portal/desktop/request/1/start_1"]

        open_res = MagicMock()
        open_res.message_type = 1
        open_res.body = [0]
        open_res.unix_fds = [42]

        mock_bus.call.side_effect = [create_res, select_res, start_res, open_res]

        capture = ScreenCastCapture(parent_window="wayland:terminal-win-1")

        # Mock wait_for_request
        async def mock_wait(path, timeout=60.0):
            if "create" in path:
                return 0, {"session_handle": MagicMock(value="/org/freedesktop/portal/desktop/session/1/sess_1")}
            elif "select" in path:
                return 0, {}
            elif "start" in path:
                stream_node = [99, {}]
                return 0, {"streams": [stream_node], "restore_token": "new_tok"}
            return 0, {}

        capture._wait_for_request = mock_wait

        def mock_start_pipe():
            capture._frame_event.set()
        capture._start_gstreamer_pipeline = mock_start_pipe

        with patch.dict(os.environ, {"XDG_ACTIVATION_TOKEN": "test-wayland-act-token"}):
            success = await capture._setup_session(restore_token=None, timeout=30.0)

        self.assertTrue(success)
        self.assertEqual(capture._node_id, 99)
        self.assertEqual(capture._pipewire_fd, 42)

        # Inspect Start call
        start_call_msg = mock_bus.call.call_args_list[2][0][0]
        self.assertEqual(start_call_msg.member, "Start")
        # Body: [session_handle, parent_window, start_options]
        self.assertEqual(start_call_msg.body[0], "/org/freedesktop/portal/desktop/session/1/sess_1")
        self.assertEqual(start_call_msg.body[1], "wayland:terminal-win-1")
        options = start_call_msg.body[2]
        self.assertIn("handle_token", options)
        self.assertIn("activation_token", options)
        self.assertEqual(options["activation_token"].value, "test-wayland-act-token")

    @patch("computer.screencast.MessageBus")
    async def test_setup_session_negotiates_unix_fd(self, mock_bus_cls):
        """Verify MessageBus is initialized with negotiate_unix_fd=True for PipeWire fd passing."""
        mock_bus = AsyncMock()
        mock_bus.disconnect = MagicMock()
        mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)

        capture = ScreenCastCapture()

        def mock_start_pipe():
            capture._frame_event.set()
        capture._start_gstreamer_pipeline = mock_start_pipe

        async def mock_wait(path, timeout=60.0):
            if "create" in path:
                return 0, {"session_handle": MagicMock(value="/session/1")}
            elif "start" in path:
                return 0, {"streams": [[10, {}]]}
            return 0, {}

        capture._wait_for_request = mock_wait

        # Mock bus.call responses for CreateSession, SelectSources, Start, OpenPipeWireRemote
        create_res = MagicMock(message_type=1, body=["/request/create_1"])
        select_res = MagicMock(message_type=1, body=["/request/select_1"])
        start_res = MagicMock(message_type=1, body=["/request/start_1"])
        open_res = MagicMock(message_type=1, body=[15])
        mock_bus.call.side_effect = [create_res, select_res, start_res, open_res]

        await capture._setup_session(restore_token=None, timeout=5.0)

        mock_bus_cls.assert_called_once()
        _, kwargs = mock_bus_cls.call_args
        self.assertTrue(kwargs.get("negotiate_unix_fd"))

    async def test_wait_for_request_signal_handler_cleanup_and_timeout_close(self):
        """Verify _wait_for_request removes signal handlers in finally and sends Close on timeout."""
        capture = ScreenCastCapture()
        mock_bus = AsyncMock()
        # Message handler methods are synchronous in dbus-next
        mock_bus.add_message_handler = MagicMock()
        mock_bus.remove_message_handler = MagicMock()
        capture._bus = mock_bus

        # Test timeout triggers Close request and removes handler
        with self.assertRaises(asyncio.TimeoutError):
            await capture._wait_for_request("/req/path/timeout", timeout=0.01)

        # Verify add and remove message handler called symmetrically
        mock_bus.add_message_handler.assert_called_once()
        mock_bus.remove_message_handler.assert_called_once()

        # Verify Close was called on the request path
        close_call = mock_bus.call.call_args[0][0]
        self.assertEqual(close_call.member, "Close")
        self.assertEqual(close_call.path, "/req/path/timeout")


if __name__ == "__main__":
    unittest.main()
