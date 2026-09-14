"""Window management capabilities for Qubit."""

import subprocess
from window_manager.manager import get_window_manager

window_manager = get_window_manager()

_current_window = None
_previous_window = None


def _invalidate_screen(action: str) -> None:
    try:
        from agent.screen_state import screen_state
        screen_state.invalidate(reason=f"window_{action}")
    except Exception:
        pass


def get_active_window():
    """Get information about the currently active desktop window."""
    return window_manager.get_active_window()


def close_active_window():
    """Close the currently active desktop window."""
    result = window_manager.close_active_window()
    _invalidate_screen("close_active")
    return result


def close_window(app):
    """Close a window by target (app name, alias, window title, or window ID)."""
    result = window_manager.close_window(app)
    _invalidate_screen("close")
    return result


def minimize_window(app):
    """Minimize a window by target (app name, alias, window title, or window ID)."""
    result = window_manager.minimize_window(app)
    _invalidate_screen("minimize")
    return result


def maximize_window(app):
    """Maximize a window by target (app name, alias, window title, or window ID)."""
    result = window_manager.maximize_window(app)
    _invalidate_screen("maximize")
    return result


def focus_window(app):
    """Focus a window by target (app name, alias, window title, or window ID)."""
    global _current_window, _previous_window

    current = get_active_window()

    result = window_manager.focus_window(app)
    _invalidate_screen("focus")

    if not result or "No window found" in result:
        return result

    new_window = get_active_window()

    if current and new_window:
        current_id = current.get("uuid") or current.get("app")
        new_id = new_window.get("uuid") or new_window.get("app")

        if current_id != new_id:
            _previous_window = current
            _current_window = new_window

    return result

def focus_previous_window():
    global _current_window, _previous_window

    if not _previous_window:
        return "No previous window found."

    target = _previous_window.get("uuid") or _previous_window.get("app")

    if not target:
        return "Previous window has no valid target."

    result = window_manager.focus_window(target)
    _invalidate_screen("focus_previous")

    if result and "No window found" not in result and "Could not" not in result:
        _current_window, _previous_window = _previous_window, _current_window

    return result