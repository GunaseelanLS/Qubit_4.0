"""Expose the 10 public capability functions for convenient imports. Do not
change or wrap their implementations; they must continue to execute the
same underlying functions."""

from .applications import open_app
from .system import get_current_time, get_current_date, get_battery_status
from .windows import (
    get_active_window,
    close_active_window,
    focus_window,
    close_window,
    minimize_window,
    maximize_window,
)

__all__ = [
    "open_app",
    "get_current_time",
    "get_current_date",
    "get_battery_status",
    "get_active_window",
    "close_active_window",
    "focus_window",
    "close_window",
    "minimize_window",
    "maximize_window",
]
