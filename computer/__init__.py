"""Computer interaction package for Qubit."""

from .screen import (
    screenshot,
    delete_screenshot,
    cleanup_screenshots,
)
from .screencast import ScreenCastCapture

__all__ = [
    "screenshot",
    "delete_screenshot",
    "cleanup_screenshots",
    "ScreenCastCapture",
]
