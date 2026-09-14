"""Unit and integration tests for Screen Awareness and Observation State system."""

import asyncio
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

# Add project root to sys.path so tests can be run directly from tests/ or IDEs
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.screen_state import ScreenState, screen_state
from capabilities.screen import (
    screenshot,
    set_image_sender,
)


class TestScreenStateUnit(unittest.TestCase):
    """Unit tests for ScreenState logic."""

    def setUp(self):
        self.state = ScreenState(default_ttl_seconds=30.0)

    def test_initial_state(self):
        self.assertIsNone(self.state.get_current_observation())
        needed, reason = self.state.should_capture()
        self.assertTrue(needed)
        self.assertEqual(reason, "no_observation")

    def test_record_observation(self):
        window_info = {"app": "brave", "caption": "GitHub - Repo", "uuid": "w1"}
        obs = self.state.record_observation(window_info, context={"reason": "initial"})
        self.assertIsNotNone(obs)
        self.assertTrue(obs.is_valid)
        self.assertEqual(obs.app, "brave")
        self.assertEqual(obs.caption, "GitHub - Repo")
        self.assertEqual(obs.uuid, "w1")
        self.assertEqual(self.state.get_current_observation(), obs)

    def test_reuse_same_window(self):
        window_info = {"app": "brave", "caption": "GitHub - Repo", "uuid": "w1"}
        self.state.record_observation(window_info)

        needed, reason = self.state.should_capture(current_window=window_info)
        self.assertFalse(needed)
        self.assertEqual(reason, "observation_reusable")

    def test_stale_observation(self):
        window_info = {"app": "brave", "caption": "GitHub - Repo", "uuid": "w1"}
        old_time = time.time() - 35.0  # past 30s TTL
        self.state.record_observation(window_info, timestamp=old_time)

        needed, reason = self.state.should_capture(current_window=window_info)
        self.assertTrue(needed)
        self.assertEqual(reason, "observation_stale")

    def test_window_changed(self):
        window_a = {"app": "brave", "caption": "GitHub", "uuid": "w1"}
        window_b = {"app": "vscode", "caption": "main.py", "uuid": "w2"}
        self.state.record_observation(window_a)

        needed, reason = self.state.should_capture(current_window=window_b)
        self.assertTrue(needed)
        self.assertEqual(reason, "window_changed")

    def test_same_app_different_caption(self):
        window_a = {"app": "brave", "caption": "Page 1", "uuid": "w1"}
        window_b = {"app": "brave", "caption": "Page 2", "uuid": "w1"}
        self.state.record_observation(window_a)

        needed, reason = self.state.should_capture(current_window=window_b)
        self.assertTrue(needed)
        self.assertEqual(reason, "window_changed")

    def test_force_capture(self):
        window_info = {"app": "brave", "caption": "GitHub - Repo", "uuid": "w1"}
        self.state.record_observation(window_info)

        needed, reason = self.state.should_capture(force=True, current_window=window_info)
        self.assertTrue(needed)
        self.assertEqual(reason, "explicit_fresh_request")

    def test_invalidation(self):
        window_info = {"app": "brave", "caption": "GitHub - Repo", "uuid": "w1"}
        self.state.record_observation(window_info)

        self.state.invalidate(reason="window_focus_changed")
        needed, reason = self.state.should_capture(current_window=window_info)
        self.assertTrue(needed)
        self.assertEqual(reason, "observation_invalid")


class TestScreenshotCapabilityIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests for capabilities/screen.py and Screen Awareness."""

    def setUp(self):
        screen_state.clear()
        self.sent_images = []

        async def mock_sender(data: bytes):
            self.sent_images.append(data)

        self.mock_sender = mock_sender
        set_image_sender(mock_sender)

        # Temp directory for fake screenshot files
        self.temp_dir = tempfile.TemporaryDirectory()

        # Patch delete_screenshot for mock test files
        self.delete_patch = patch("capabilities.screen.delete_screenshot")
        self.mock_delete_screenshot = self.delete_patch.start()

        # Patch ocr_engine for mock test files
        self.ocr_patch = patch("capabilities.screen.ocr_engine.extract_from_file")
        self.mock_extract = self.ocr_patch.start()
        self.mock_extract.return_value = {"full_text": "", "lines": [], "words": []}

        # Mock ScreenCastCapture.start so screenshot fallback is tested deterministically
        self.screencast_patch = patch(
            "capabilities.screen.ScreenCastCapture.start",
            side_effect=RuntimeError("ScreenCast disabled for screenshot fallback tests"),
        )
        self.screencast_patch.start()

    def tearDown(self):
        self.screencast_patch.stop()
        self.ocr_patch.stop()
        self.delete_patch.stop()
        set_image_sender(None)
        screen_state.clear()
        self.temp_dir.cleanup()

    def _create_fake_screenshot(self) -> Path:
        p = Path(self.temp_dir.name) / f"test_{time.time_ns()}.png"
        # Minimal valid 1x1 PNG bytes
        valid_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00"
            b"\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        p.write_bytes(valid_png)
        return p

    @patch("capabilities.screen.computer_screenshot")
    @patch("capabilities.screen.get_active_window")
    async def test_scenario_1_and_2_initial_capture_and_reuse(
        self, mock_get_active_window, mock_comp_screenshot
    ):
        """TEST 1: Initial call captures screenshot & creates observation.
        TEST 2: Follow-up question on same window reuses observation.
        """
        active_window = {"app": "brave", "caption": "Docs", "uuid": "w1"}
        mock_get_active_window.return_value = active_window
        fake_file = self._create_fake_screenshot()
        mock_comp_screenshot.return_value = fake_file

        # TEST 1: Initial call
        res1 = await screenshot()
        self.assertIn("Screenshot captured and sent to Gemini successfully", res1)
        self.assertEqual(mock_comp_screenshot.call_count, 1)
        self.assertEqual(len(self.sent_images), 1)

        # Observation state must now be valid and track active window
        obs = screen_state.get_current_observation()
        self.assertIsNotNone(obs)
        self.assertTrue(obs.is_valid)
        self.assertEqual(obs.app, "brave")
        self.assertEqual(obs.caption, "Docs")

        # TEST 2: Consecutive call on same unchanged screen
        res2 = await screenshot()
        self.assertIn("Screen observation reused", res2)
        # computer_screenshot should NOT have been called again!
        self.assertEqual(mock_comp_screenshot.call_count, 1)
        # No duplicate image sent!
        self.assertEqual(len(self.sent_images), 1)

    @patch("capabilities.screen.computer_screenshot")
    @patch("capabilities.screen.get_active_window")
    async def test_scenario_3_and_5_force_fresh_screenshot(
        self, mock_get_active_window, mock_comp_screenshot
    ):
        """TEST 3 & 5: When force=True is passed, capture a fresh screenshot."""
        active_window = {"app": "brave", "caption": "Docs", "uuid": "w1"}
        mock_get_active_window.return_value = active_window

        fake_file1 = self._create_fake_screenshot()
        mock_comp_screenshot.return_value = fake_file1
        await screenshot()
        self.assertEqual(mock_comp_screenshot.call_count, 1)

        # Force fresh screenshot (explicit request)
        fake_file2 = self._create_fake_screenshot()
        mock_comp_screenshot.return_value = fake_file2
        res_forced = await screenshot(force=True)

        self.assertIn("Screenshot captured and sent to Gemini successfully", res_forced)
        self.assertEqual(mock_comp_screenshot.call_count, 2)
        self.assertEqual(len(self.sent_images), 2)

    @patch("capabilities.screen.computer_screenshot")
    @patch("capabilities.screen.get_active_window")
    async def test_scenario_4_window_change_triggers_capture(
        self, mock_get_active_window, mock_comp_screenshot
    ):
        """TEST 4: Changing the active window causes a new screenshot to be captured."""
        window_1 = {"app": "brave", "caption": "Docs", "uuid": "w1"}
        window_2 = {"app": "terminal", "caption": "bash", "uuid": "w2"}

        mock_get_active_window.return_value = window_1
        fake_file1 = self._create_fake_screenshot()
        mock_comp_screenshot.return_value = fake_file1
        await screenshot()
        self.assertEqual(mock_comp_screenshot.call_count, 1)

        # Active window switches to Terminal
        mock_get_active_window.return_value = window_2
        fake_file2 = self._create_fake_screenshot()
        mock_comp_screenshot.return_value = fake_file2

        res_new_win = await screenshot()
        self.assertIn("Screenshot captured and sent to Gemini successfully", res_new_win)
        self.assertEqual(mock_comp_screenshot.call_count, 2)
        self.assertEqual(screen_state.get_current_observation().app, "terminal")

    @patch("capabilities.screen.delete_screenshot")
    @patch("capabilities.screen.computer_screenshot")
    @patch("capabilities.screen.get_active_window")
    async def test_scenario_6_screenshot_cleanup(
        self, mock_get_active_window, mock_comp_screenshot, mock_delete_screenshot
    ):
        """TEST 6: Screenshot file is guaranteed to be deleted after sending."""
        mock_get_active_window.return_value = {"app": "brave"}
        fake_file = self._create_fake_screenshot()
        mock_comp_screenshot.return_value = fake_file

        await screenshot()
        mock_delete_screenshot.assert_called_once_with(fake_file)

    @patch("capabilities.screen.computer_screenshot")
    @patch("capabilities.screen.get_active_window")
    async def test_error_handling_invalidates_observation(
        self, mock_get_active_window, mock_comp_screenshot
    ):
        """TEST error handling: Failed capture or send does not leave valid state."""
        mock_get_active_window.return_value = {"app": "brave"}
        mock_comp_screenshot.side_effect = RuntimeError("Portal failure")

        with self.assertRaises(RuntimeError):
            await screenshot()

        obs = screen_state.get_current_observation()
        # Must not be a valid observation
        self.assertTrue(obs is None or not obs.is_valid)

    @patch("capabilities.windows.window_manager")
    def test_scenario_7_window_tools_invalidate_screen_state(self, mock_wm):
        """TEST 7: Window actions invalidate screen observation state."""
        from capabilities.windows import (
            focus_window,
            close_window,
            close_active_window,
            minimize_window,
            maximize_window,
        )

        mock_wm.focus_window.return_value = "Focused"
        mock_wm.close_window.return_value = "Closed"
        mock_wm.close_active_window.return_value = "Closed active"
        mock_wm.minimize_window.return_value = "Minimized"
        mock_wm.maximize_window.return_value = "Maximized"

        # Record valid observation
        screen_state.record_observation({"app": "brave", "caption": "Test"})
        self.assertTrue(screen_state.get_current_observation().is_valid)

        # Focus window action
        focus_window("terminal")
        self.assertFalse(screen_state.get_current_observation().is_valid)

        # Reset & test close_window
        screen_state.record_observation({"app": "brave", "caption": "Test"})
        close_window("brave")
        self.assertFalse(screen_state.get_current_observation().is_valid)

        # Reset & test minimize_window
        screen_state.record_observation({"app": "brave", "caption": "Test"})
        minimize_window("brave")
        self.assertFalse(screen_state.get_current_observation().is_valid)


if __name__ == "__main__":
    unittest.main()
