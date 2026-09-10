"""Window management capabilities for Qubit."""

from window_manager.manager import get_window_manager

window_manager = get_window_manager()


def get_active_window():
    """Get information about the currently active desktop window."""
    return window_manager.get_active_window()


def close_active_window():
    """Close the currently active desktop window."""
    return window_manager.close_active_window()


def focus_window(app):
    """Focus a window by target (app name, alias, window title, or window ID)."""
    return window_manager.focus_window(app)


def close_window(app):
    """Close a window by target (app name, alias, window title, or window ID)."""
    return window_manager.close_window(app)


def minimize_window(app):
    """Minimize a window by target (app name, alias, window title, or window ID)."""
    return window_manager.minimize_window(app)


def maximize_window(app):
    """Maximize a window by target (app name, alias, window title, or window ID)."""
    return window_manager.maximize_window(app)
