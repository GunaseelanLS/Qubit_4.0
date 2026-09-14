"""Application management capabilities for Qubit."""

import os
import re
import subprocess
import time
import psutil

Apps = {
    "brave": {
        "open": ["brave-browser-stable"],
        "new": ["brave-browser-stable", "--new-window"],
        "process": "/opt/brave.com/brave/brave",  # Actual process name of application while running
        "aliases": ["brave", "brave browser"],
    },
    "vscode": {
        "open": ["code"],
        "new": ["code", "--new-window"],
        "process": "/usr/share/code/code",
        "aliases": ["code", "vs code", "visual studio code"],
    },
    "terminal": {
        "open": ["ptyxis"],
        "new": ["ptyxis"],
        "process": "ptyxis",
        "aliases": ["cmd", "terminal", "command prompt"],
    },
    "dolphin": {
        "open": ["dolphin"],
        "new": ["dolphin"],
        "process": "dolphin",
        "aliases": ["file manager", "files", "dolphin"],
    },
    "notes": {
        "open": ["kate"],
        "new": ["kate", "-n"],
        "process": "kate",
        "aliases": ["text editor", "notes", "kate", "gate"],
    },
}

opened_apps = {}
new_instances = {}


def is_running(process_name):
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
            if process_name in cmdline:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False


def _desktop_dirs():
    return [
        "/var/lib/flatpak/exports/share/applications",
        os.path.expanduser("~/.local/share/applications"),
        "/usr/local/share/applications",
        "/usr/share/applications",
    ]


def _desktop_fields(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return "", "", "", ""

    name = re.search(r"(?m)^Name=(.*)$", text)
    exec_ = re.search(r"(?m)^Exec=(.*)$", text)
    wm = re.search(r"(?m)^StartupWMClass=(.*)$", text)

    return (
        os.path.basename(path)[:-8].lower(),
        (name.group(1).strip() if name else "").lower(),
        (exec_.group(1).strip() if exec_ else "").lower(),
        (wm.group(1).strip() if wm else "").lower(),
    )


def _find_desktop(app):
    app = app.lower().strip()
    tokens = [t for t in re.split(r"[^a-z0-9]+", app) if len(t) >= 2]

    best = None
    best_score = -1

    for d in _desktop_dirs():
        try:
            entries = os.listdir(d)
        except OSError:
            continue

        for entry in entries:
            if not entry.endswith(".desktop"):
                continue

            base, name, exec_, wm = _desktop_fields(os.path.join(d, entry))
            base_tokens = [t for t in re.split(r"[^a-z0-9]+", base) if t]
            base_last_dot = base.rsplit(".", 1)[-1]

            score = -1

            if base == app or name == app:
                score = 100
            elif base_last_dot == app:
                score = 80
            elif name.startswith(app):
                score = 60
            elif base_tokens and base_tokens[0] == app:
                score = 40
            elif base.startswith(app):
                score = 30
            elif app.startswith(base) and len(base) >= 4:
                score = 20
            elif tokens and name.startswith(tokens[0]):
                score = 50
            elif tokens and all(t in f"{base} {name} {wm}" for t in tokens):
                score = 10
            elif app in f"{base} {name} {exec_} {wm}":
                score = 5

            if score > best_score:
                best_score = score
                best = os.path.join(d, entry)

    return best


def _invalidate_screen(action: str) -> None:
    try:
        from agent.screen_state import screen_state
        screen_state.invalidate(reason=action)
    except Exception:
        pass


def open_app(app, mode="default"):
    app = app.lower()

    if app not in Apps:
        desktop = _find_desktop(app)

        if not desktop:
            return f"{app} is not supported."

        subprocess.Popen(["gio", "launch", desktop], start_new_session=True)
        _invalidate_screen(f"open_app:{app}")
        return f"{app} opened successfully!"

    if mode == "default" and is_running(Apps[app]["process"]):
        return f"{app} is already open."

    before = {
        p.pid
        for p in psutil.process_iter(["pid", "cmdline"])
    }

    command = (
        Apps[app]["new"]
        if mode == "new_window"
        else Apps[app]["open"]
    )

    subprocess.Popen(command, start_new_session=True)
    _invalidate_screen(f"open_app:{app}")

    time.sleep(1.5)

    candidates = []

    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = proc.info["pid"]
            cmdline = " ".join(proc.info["cmdline"] or [])

            if (
                pid not in before
                and Apps[app]["process"] in cmdline
            ):
                candidates.append(pid)

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess
        ):
            pass

    if not candidates:
        if mode == "new_window":
            return f"Could not detect new {app} process."
        return f"Could not detect {app} process."

    if mode == "new_window":
        if app not in new_instances:
            new_instances[app] = []

        new_instances[app].append(candidates)
        print(f"{app} new PIDs = {candidates}")
        return f"New {app} opened successfully!"

    opened_apps[app] = candidates
    return f"{app} opened successfully!"
