"""Tests for strictly demand-driven screen awareness policy (ScreenAccessPolicy).

Validates:
1. Normal conversation ("What's the weather?") -> Screen capture is blocked (no ScreenCast, no OCR).
2. Explicit screen question ("What's on my screen?") -> Screen capture is allowed (ScreenCast, OCR, Vision).
3. Screen-dependent action ("Click the Settings button") -> Screen capture is allowed.
4. Programmatic scope (grant_access context manager) -> Screen capture is allowed.
5. Registered screen-dependent tool -> Screen capture is allowed.
6. TTL expiration -> Old query expires and does not leak access.
7. Verification that OCR + Gemini Vision processing still receives the frame when granted.
"""

import sys
from pathlib import Path

# Bootstrap project root into sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agent.screen_policy import ScreenAccessPolicy, screen_policy
from capabilities.screen import screenshot, set_image_sender
from agent.screen_state import screen_state


class TestScreenAccessPolicyUnit(unittest.TestCase):
    """Unit tests for ScreenAccessPolicy intent parsing, scoping, and gating logic."""

    def setUp(self):
        self.policy = ScreenAccessPolicy(query_ttl=1.0)
        self.policy.clear()

    def tearDown(self):
        self.policy.clear()

    def test_general_conversation_intent_denied(self):
        """Conversational queries without screen context must be denied."""
        queries = [
            "What's the weather in Chennai?",
            "How is the weather today?",
            "Tell me a joke.",
            "Who was Albert Einstein?",
            "How do I write a quicksort algorithm in Python?",
            "Write an essay about AI.",
            "Calculate 25 * 40.",
            "Good morning Qubit!",
            "What time is it right now?",
            "Explain quantum gravity.",
        ]
        for q in queries:
            allowed, reason = self.policy.evaluate_intent(q)
            self.assertFalse(allowed, f"Expected '{q}' to be denied, but got allowed with reason: {reason}")

    def test_screen_query_intent_allowed(self):
        """Direct screen queries and visual deictic references must be allowed."""
        queries = [
            "What's on my screen?",
            "What do you see on the display?",
            "Look at my screen please.",
            "Inspect the active window.",
            "Read the text on screen.",
            "What does the terminal say?",
            "Can you see this error message?",
            "Look at this code.",
            "Why is this red and failing?",
            "Take a screenshot.",
            "Screen la enna irukku?",
            "Idha paaru.",
        ]
        for q in queries:
            allowed, reason = self.policy.evaluate_intent(q)
            self.assertTrue(allowed, f"Expected '{q}' to be allowed, but got denied with reason: {reason}")

    def test_screen_action_intent_allowed(self):
        """Actions targeting on-screen elements must be allowed."""
        actions = [
            "Click the Settings button.",
            "Press the submit button.",
            "Tap on the cancel button.",
            "Select option 2 from the dropdown menu.",
            "Click 'Save and Continue'",
            "Find the login icon.",
            "Type into the search bar.",
        ]
        for a in actions:
            allowed, reason = self.policy.evaluate_intent(a)
            self.assertTrue(allowed, f"Expected '{a}' to be allowed, but got denied with reason: {reason}")

    def test_programmatic_scope_grant(self):
        """grant_access context manager must allow access regardless of query."""
        self.policy.record_user_query("What's the weather?")
        # Without scope: denied
        allowed, _ = self.policy.evaluate_access()
        self.assertFalse(allowed)

        # Within programmatic scope: allowed
        with self.policy.grant_access(requester="test_runner", reason="automated_gui_test"):
            allowed, reason = self.policy.evaluate_access()
            self.assertTrue(allowed)
            self.assertIn("programmatic grant active", reason)

        # After scope ends: denied again
        allowed, _ = self.policy.evaluate_access()
        self.assertFalse(allowed)

    def test_screen_dependent_tool_registration(self):
        """Tools registered as screen-dependent must be permitted."""
        self.policy.record_user_query("What is the weather?")
        self.policy.register_screen_dependent_tool("custom_clicker")

        with self.policy.active_tool_context("custom_clicker", {}):
            allowed, reason = self.policy.evaluate_access()
            self.assertTrue(allowed)
            self.assertIn("custom_clicker", reason)

        # Unregistered tool: denied
        with self.policy.active_tool_context("get_weather_tool", {}):
            allowed, _ = self.policy.evaluate_access()
            self.assertFalse(allowed)

    def test_query_ttl_expiration(self):
        """User queries must expire after query_ttl seconds."""
        policy_short_ttl = ScreenAccessPolicy(query_ttl=0.1)
        policy_short_ttl.set_interactive_mode(True)
        policy_short_ttl.record_user_query("What's on my screen?")

        # Immediately: allowed
        allowed, _ = policy_short_ttl.evaluate_access()
        self.assertTrue(allowed)

        # After TTL expires: query is gone, access denied
        time.sleep(0.15)
        allowed, reason = policy_short_ttl.evaluate_access()
        self.assertFalse(allowed)
        self.assertIn("Denied", reason)


class TestScreenAccessPolicyIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests verifying end-to-end gating in capabilities.screen.screenshot."""

    async def asyncSetUp(self):
        screen_state.clear()
        screen_policy.clear()
        screen_policy.set_interactive_mode(True)
        self.mock_sender = AsyncMock()
        set_image_sender(self.mock_sender)

    async def asyncTearDown(self):
        set_image_sender(None)
        screen_state.clear()
        screen_policy.clear()

    @patch("capabilities.screen.ocr_engine.extract_from_bytes")
    @patch("capabilities.screen.ScreenCastCapture")
    async def test_normal_conversation_blocks_screencast_and_ocr(
        self, mock_sc_class, mock_ocr_extract
    ):
        """When user asks 'What's the weather?', screenshot() is skipped: NO screencast, NO OCR."""
        screen_policy.record_user_query("What's the weather in Chennai today?")

        result = await screenshot()

        # Must report skipped
        self.assertIn("Screen capture skipped", result)
        self.assertIn("does not require screen context", result)

        # ScreenCastCapture must NOT have been instantiated or started
        mock_sc_class.assert_not_called()

        # OCR must NOT have been executed
        mock_ocr_extract.assert_not_called()

        # Image must NOT have been sent to Gemini
        self.mock_sender.assert_not_called()

        # ScreenState observation must NOT exist
        self.assertIsNone(screen_state.get_current_observation())

    @patch("capabilities.screen.get_active_window")
    @patch("capabilities.screen.ocr_engine.extract_from_bytes")
    @patch("capabilities.screen.ScreenCastCapture")
    async def test_screen_question_activates_screencast_and_ocr(
        self, mock_sc_class, mock_ocr_extract, mock_window
    ):
        """When user asks 'What's on my screen?', screenshot() captures frame, runs OCR, sends to Gemini."""
        mock_window.return_value = {"app": "vscode", "caption": "main.py"}
        fake_frame = b"\x89PNGfakeframe"

        mock_sc_instance = MagicMock()
        mock_sc_instance.is_active = True
        mock_sc_instance.start = AsyncMock(return_value=True)
        mock_sc_instance.stop = AsyncMock()
        mock_sc_instance.get_latest_frame = AsyncMock(return_value=fake_frame)
        mock_sc_class.return_value = mock_sc_instance

        mock_ocr_extract.return_value = {
            "full_text": "def test_function(): pass",
            "lines": [{"text": "def test_function(): pass", "bbox": [0, 0, 100, 20], "confidence": 0.99}],
            "words": [],
        }

        screen_policy.record_user_query("What's on my screen right now?")

        result = await screenshot()

        # Must indicate successful capture
        self.assertIn("Screen frame captured via ScreenCast", result)
        self.assertIn("vscode", result)
        self.assertIn("def test_function(): pass", result)

        # ScreenCast must have been started
        mock_sc_instance.start.assert_called_once()
        mock_sc_instance.get_latest_frame.assert_called_once()

        # OCR must have processed the exact captured frame bytes
        mock_ocr_extract.assert_called_once_with(fake_frame)

        # Gemini Vision sender must have received the frame
        self.mock_sender.assert_called_once_with(fake_frame)

        # Observation must be stored in screen_state
        obs = screen_state.get_current_observation()
        self.assertIsNotNone(obs)
        self.assertIsNotNone(obs.perception)
        self.assertIn("def test_function(): pass", obs.ocr_data["full_text"])

    @patch("capabilities.screen.get_active_window")
    @patch("capabilities.screen.ocr_engine.extract_from_bytes")
    @patch("capabilities.screen.ScreenCastCapture")
    async def test_screen_dependent_action_activates_capture(
        self, mock_sc_class, mock_ocr_extract, mock_window
    ):
        """When user says 'Click the Settings button', screenshot() is activated."""
        mock_window.return_value = {"app": "gnome-control-center", "caption": "Settings"}
        fake_frame = b"\x89PNGsettingsframe"

        mock_sc_instance = MagicMock()
        mock_sc_instance.is_active = True
        mock_sc_instance.start = AsyncMock(return_value=True)
        mock_sc_instance.stop = AsyncMock()
        mock_sc_instance.get_latest_frame = AsyncMock(return_value=fake_frame)
        mock_sc_class.return_value = mock_sc_instance

        mock_ocr_extract.return_value = {
            "full_text": "Settings Wi-Fi Bluetooth Network",
            "lines": [{"text": "Settings", "bbox": [10, 10, 80, 20], "confidence": 0.98}],
            "words": [],
        }

        screen_policy.record_user_query("Click the Settings button")

        result = await screenshot()

        self.assertIn("Screen frame captured via ScreenCast", result)
        self.assertIn("Settings", result)
        mock_sc_instance.start.assert_called_once()
        mock_ocr_extract.assert_called_once_with(fake_frame)
        self.mock_sender.assert_called_once_with(fake_frame)

    @patch("capabilities.screen.get_active_window")
    @patch("capabilities.screen.ocr_engine.extract_from_bytes")
    @patch("capabilities.screen.ScreenCastCapture")
    async def test_programmatic_grant_scope_activates_capture(
        self, mock_sc_class, mock_ocr_extract, mock_window
    ):
        """A screen-dependent tool executing with grant_access() activates capture even with no user query."""
        mock_window.return_value = {"app": "terminal", "caption": "bash"}
        fake_frame = b"\x89PNGtermframe"

        mock_sc_instance = MagicMock()
        mock_sc_instance.is_active = True
        mock_sc_instance.start = AsyncMock(return_value=True)
        mock_sc_instance.stop = AsyncMock()
        mock_sc_instance.get_latest_frame = AsyncMock(return_value=fake_frame)
        mock_sc_class.return_value = mock_sc_instance

        mock_ocr_extract.return_value = {
            "full_text": "user@host:~$ ls -la",
            "lines": [],
            "words": [],
        }

        # Clear any user query
        screen_policy.clear_user_query()

        # Without grant: denied in interactive mode
        res_denied = await screenshot()
        self.assertIn("Screen capture skipped", res_denied)

        # Inside programmatic grant: allowed
        with screen_policy.grant_access(requester="ui_automator", reason="locate_terminal_prompt"):
            res_allowed = await screenshot()
            self.assertIn("Screen frame captured via ScreenCast", res_allowed)
            mock_sc_instance.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
