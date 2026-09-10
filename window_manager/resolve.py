import re

ALIASES = {
    "brave": ["brave-browser", "brave"],
    "brave browser": ["brave-browser", "brave"],
    "vscode": ["code", "vscode"],
    "code": ["code", "vscode"],
    "visual studio code": ["code", "vscode"],
    "virtual studio code": ["code", "vscode"],
    "terminal": ["ptyxis", "org.gnome.ptyxis", "konsole", "org.kde.konsole",
                 "gnome-terminal", "x-terminal-emulator", "cool-retro-term",
                 "alacritty", "kitty", "wezterm", "windows terminal"],
    "cmd": ["ptyxis", "konsole", "gnome-terminal", "cool-retro-term"],
    "command prompt": ["ptyxis", "konsole", "gnome-terminal", "cool-retro-term"],
    "console": ["ptyxis", "konsole", "gnome-terminal", "cool-retro-term"],
    "dolphin": ["dolphin", "org.kde.dolphin"],
    "files": ["dolphin", "org.kde.dolphin", "nautilus", "org.gnome.nautilus"],
    "file manager": ["dolphin", "org.kde.dolphin", "nautilus", "org.gnome.nautilus"],
    "nautilus": ["nautilus", "org.gnome.nautilus"],
    "notes": ["kate", "org.kde.kate"],
    "text editor": ["kate", "org.kde.kate", "gedit", "org.gnome.texteditor"],
    "kate": ["kate", "org.kde.kate"],
    "gate": ["kate", "org.kde.kate"],
    "firefox": ["firefox", "firefox-esr"],
    "chrome": ["google-chrome", "chromium"],
    "intellij": ["jetbrains-idea", "idea", "intellij-idea"],
    "idea": ["jetbrains-idea", "idea", "intellij-idea"],
    "pdf": ["evince", "org.gnome.evince", "papers", "org.gnome.papers"],
    "vlc": ["vlc", "org.videolan.vlc"],
    "settings": ["gnome-control-center", "org.gnome.settings",
                 "systemsettings", "org.kde.systemsettings"],
    "system settings": ["systemsettings", "org.kde.systemsettings", "gnome-control-center"],
    "calculator": ["gnome-calculator", "org.gnome.calculator", "kcalc"],
    "system monitor": ["org.gnome.systemmonitor", "gnome-system-monitor", "plasma-systemmonitor"],
    "weather": ["org.gnome.weather", "gnome-weather"],
    "maps": ["org.gnome.maps", "gnome-maps"],
    "clock": ["org.gnome.clocks", "gnome-clocks"],
    "calendar": ["org.gnome.calendar", "gnome-calendar"],
    "photos": ["org.gnome.photos", "gnome-photos"],
}


def expand(target):
    """Return ordered candidate identifiers for a target string.

    A target may be a window ID/UUID, an app name/alias, or a window title.
    Candidates are: the raw target, its alias classes, then word tokens.
    Used by backends to resolve 'vscode' -> 'code', 'terminal' -> 'ptyxis',
    while also letting exact IDs and titles pass through.
    """
    key = str(target).lower().strip()

    if not key:
        return []

    out = [key]

    for alias, classes in ALIASES.items():
        if key == alias or key in alias or alias in key:
            out.extend(classes)

    for token in re.split(r"[^a-z0-9]+", key):
        if len(token) >= 3:
            out.append(token)

    seen = set()
    result = []

    for cand in out:
        if cand not in seen:
            seen.add(cand)
            result.append(cand)

    return result