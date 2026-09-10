import os
from .kde import KDEWindowManager
from .gnome import GNOMEWindowManager

def get_window_manager():
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    if "kde" in desktop:
        return KDEWindowManager()

    if "gnome" in desktop:
        return GNOMEWindowManager()

    raise RuntimeError(f"Unsupported desktop environment: {desktop}")