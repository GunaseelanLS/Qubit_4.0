"""Unit and integration tests for OCR and Screen Perception layer."""

import asyncio
import os
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

from agent.screen_state import screen_state
from capabilities.screen import screenshot, set_image_sender
from perception.ocr import ocr_engine
from perception.screen import create_screen_perception


class TestOCREngine(unittest.TestCase):
    """Unit tests for native OCREngine."""

    def test_engine_initialization(self):
        self.assertIsNotNone(ocr_engine)
        self.assertTrue(os.path.isdir(ocr_engine.tessdata_dir))

    def test_structured_output_format(self):
        # Test on system pixmap
        logo_path = "/usr/share/pixmaps/fedora-gdm-logo.png"
        if os.path.exists(logo_path):
            result = ocr_engine.extract_from_file(logo_path)
            self.assertIn("full_text", result)
            self.assertIn("lines", result)
            self.assertIn("words", result)
            self.assertIsInstance(result["lines"], list)
            self.assertIsInstance(result["words"], list)
            for item in result["lines"] + result["words"]:
                self.assertIn("text", item)
                self.assertIn("bbox", item)
                self.assertIn("confidence", item)
                self.assertEqual(len(item["bbox"]), 4)
                self.assertGreaterEqual(item["confidence"], 0.0)
                self.assertLessEqual(item["confidence"], 1.0)


class TestScreenPerception(unittest.TestCase):
    """Unit tests for ScreenPerception data layer."""

    def setUp(self):
        self.mock_ocr_data = {
            "full_text": "gemini_live.py tool_registry.py screen.py terminal\nRunning bash",
            "lines": [
                {
                    "text": "gemini_live.py tool_registry.py screen.py terminal",
                    "bbox": [10, 10, 400, 20],
                    "confidence": 0.95,
                },
                {
                    "text": "Running bash command",
                    "bbox": [10, 40, 200, 20],
                    "confidence": 0.90,
                },
            ],
            "words": [
                {"text": "gemini_live.py", "bbox": [10, 10, 90, 20], "confidence": 0.98},
                {"text": "tool_registry.py", "bbox": [110, 10, 100, 20], "confidence": 0.96},
                {"text": "screen.py", "bbox": [220, 10, 70, 20], "confidence": 0.97},
                {"text": "terminal", "bbox": [300, 10, 60, 20], "confidence": 0.92},
            ],
        }
        self.mock_window_info = {
            "app": "code",
            "caption": "Qubit_4.0 - VS Code",
            "uuid": "w-100",
        }
        self.perception = create_screen_perception(self.mock_window_info, self.mock_ocr_data)

    def test_properties(self):
        self.assertEqual(self.perception.app, "code")
        self.assertEqual(self.perception.caption, "Qubit_4.0 - VS Code")
        self.assertEqual(self.perception.uuid, "w-100")
        self.assertEqual(len(self.perception.lines), 2)
        self.assertEqual(len(self.perception.words), 4)

    def test_find_text(self):
        found = self.perception.find_text("gemini_live.py")
        self.assertGreater(len(found), 0)
        self.assertIn("gemini_live.py", found[0]["text"])

    def test_extract_filenames(self):
        files = self.perception.extract_filenames()
        self.assertIn("gemini_live.py", files)
        self.assertIn("tool_registry.py", files)
        self.assertIn("screen.py", files)

    def test_format_gemini_context(self):
        ctx = self.perception.format_gemini_context()
        self.assertIn("[Active Window] Qubit_4.0 - VS Code (code)", ctx)
        self.assertIn("[Detected Files/Tabs]", ctx)
        self.assertIn("gemini_live.py", ctx)
        self.assertIn("[Screen Text Content (OCR)]", ctx)


class TestPerceptionScreenshotIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration test between Screen Awareness, OCR, and Screenshot capability."""

    def setUp(self):
        screen_state.clear()
        self.sent_images = []

        async def mock_sender(data: bytes):
            self.sent_images.append(data)

        self.mock_sender = mock_sender
        set_image_sender(mock_sender)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.delete_patch = patch("capabilities.screen.delete_screenshot")
        self.mock_delete_screenshot = self.delete_patch.start()

    def tearDown(self):
        self.delete_patch.stop()
        set_image_sender(None)
        screen_state.clear()
        self.temp_dir.cleanup()

    def _create_fake_screenshot(self) -> Path:
        p = Path(self.temp_dir.name) / f"test_{time.time_ns()}.png"
        p.write_bytes(b"\x89PNGfakeimage")
        return p

    @patch("capabilities.screen.ScreenCastCapture.start", side_effect=RuntimeError("ScreenCast disabled for fallback test"))
    @patch("perception.ocr.ocr_engine.extract_from_file")
    @patch("capabilities.screen.computer_screenshot")
    @patch("capabilities.screen.get_active_window")
    async def test_ocr_data_in_observation_and_reuse(
        self, mock_get_active_window, mock_comp_screenshot, mock_ocr_extract, mock_sc_start
    ):
        mock_get_active_window.return_value = {"app": "vscode", "caption": "Project"}
        mock_comp_screenshot.return_value = self._create_fake_screenshot()
        mock_ocr_extract.return_value = {
            "full_text": "gemini_live.py\nscreen.py",
            "lines": [
                {"text": "gemini_live.py", "bbox": [10, 10, 100, 20], "confidence": 0.95},
                {"text": "screen.py", "bbox": [120, 10, 80, 20], "confidence": 0.94},
            ],
            "words": [
                {"text": "gemini_live.py", "bbox": [10, 10, 100, 20], "confidence": 0.95},
                {"text": "screen.py", "bbox": [120, 10, 80, 20], "confidence": 0.94},
            ],
        }

        # 1. First capture -> calls OCR and attaches perception
        result1 = await screenshot()
        self.assertIn("Screenshot captured and sent to Gemini successfully", result1)
        self.assertIn("gemini_live.py", result1)
        self.assertEqual(mock_ocr_extract.call_count, 1)

        # Observation must contain OCR data and perception
        obs = screen_state.get_current_observation()
        self.assertIsNotNone(obs)
        self.assertIsNotNone(obs.ocr_data)
        self.assertIsNotNone(obs.perception)
        self.assertIn("gemini_live.py", obs.ocr_data["full_text"])

        # 2. Second query on unchanged window -> reuses observation AND includes cached OCR context
        result2 = await screenshot()
        self.assertIn("Screen observation reused", result2)
        self.assertIn("gemini_live.py", result2)
        # OCR must NOT have been called a second time!
        self.assertEqual(mock_ocr_extract.call_count, 1)
        self.assertEqual(mock_comp_screenshot.call_count, 1)


if __name__ == "__main__":
    unittest.main()
