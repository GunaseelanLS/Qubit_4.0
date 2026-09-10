import subprocess #Runs programms or commands
import datetime #Get date and time
import psutil  #Get system information
#It is a package that is needed to be installed for the usage
#pip3 install psutil
import time
import os
import re

from window_manager.manager import get_window_manager

window_manager = get_window_manager()

Apps = {

    "brave": {
        "open": ["brave-browser-stable"],
        "new": ["brave-browser-stable", "--new-window"],
        "process": "/opt/brave.com/brave/brave",#Actual process name of application while running
        "aliases":["brave","brave browser"]
    },

    "vscode": {
        "open": ["code"],
        "new": ["code", "--new-window"],
        "process": "/usr/share/code/code",
        "aliases":["code","vs code","visual studio code"]
    },

    "terminal": {
        "open": ["ptyxis"],
        "new": ["ptyxis"],
        "process": "ptyxis",
        "aliases":["cmd","terminal","command prompt"]
    },

    "dolphin": {
        "open": ["dolphin"],
        "new": ["dolphin"],
        "process": "dolphin",
        "aliases":["file manager","files","dolphin"]
    },

    "notes": {
        "open": ["kate"],
        "new": ["kate", "-n"],
        "process": "kate",
        "aliases":["text editor","notes","kate","gate"]
    }
}

opened_apps = {}
new_instances = {}


#Application Functions
def is_running(process_name):

    for proc in psutil.process_iter(["cmdline"]):

        try:
            cmdline = " ".join(proc.info["cmdline"] or [])

            if process_name in cmdline:
                return True

        except (psutil.NoSuchProcess,
                psutil.AccessDenied,
                psutil.ZombieProcess):
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

def open_app(app, mode="default"):

    app = app.lower()

    if app not in Apps:
        desktop = _find_desktop(app)

        if not desktop:
            return f"{app} is not supported."

        subprocess.Popen(["gio", "launch", desktop], start_new_session=True)

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



# Window Management
# Window Management
def get_active_window():
    return window_manager.get_active_window()

def close_active_window():
    return window_manager.close_active_window()

def focus_window(app):
    return window_manager.focus_window(app)

def close_window(app):
    return window_manager.close_window(app)

def minimize_window(app):
    return window_manager.minimize_window(app)

def maximize_window(app):
    return window_manager.maximize_window(app)


#System Functions
def get_current_time():
    return datetime.datetime.now().strftime("%I:%M %p")


def get_current_date():
    return datetime.datetime.now().strftime("%d-%m-%Y")


def get_battery_status():
    battery = psutil.sensors_battery()

    if battery:
        return f"Battery is at {battery.percent}%."

    return "Battery information unavailable."


#Old code using Pids
'''
def open_apps(app):

    app = app.lower()

    if app not in Apps:
        return f"{app} is not supported."

    if is_running(Apps[app]["process"]):
        return f"{app} is already open."

    before = {
        p.pid
        for p in psutil.process_iter(["pid", "cmdline"])
    }

    subprocess.Popen(Apps[app]["open"],start_new_session=True)

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
        return f"Could not detect {app} process."

    opened_apps[app] = candidates

    return f"{app} opened successfully!"

def open_new_app(app):

    app = app.lower()

    if app not in Apps:
        return f"{app} is not supported."

    before = {
        p.pid
        for p in psutil.process_iter(["pid", "cmdline"])
    }

    subprocess.Popen(Apps[app]["new"],start_new_session=True)

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
    print("Before:", len(before))
    print("Candidates:", candidates)
    if not candidates:
        return f"Could not detect new {app} process."

    if app not in new_instances:
        new_instances[app] = []

    new_instances[app].append(candidates)

    print(f"{app} new PIDs = {candidates}")

    return f"New {app} opened successfully!"'''

'''
def close_apps(app):

    app = app.lower()

    if app not in opened_apps:
        return f"{app} was not opened by Qubit."

    try:

        pids = opened_apps[app]

        for pid in pids:

            try:

                process = psutil.Process(pid)

                for child in process.children(recursive=True):
                    child.terminate()

                process.terminate()

            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                psutil.ZombieProcess
            ):
                pass

        del opened_apps[app]

        return f"{app} terminated."

    except Exception as e:
        return str(e)
    
def close_new_app(app):

    app = app.lower()

    if app not in new_instances:
        return f"No new {app} instances found."

    if len(new_instances[app]) == 0:
        return f"No new {app} instances found."

    try:

        pids = new_instances[app].pop()

        for pid in pids:

            try:

                process = psutil.Process(pid)

                for child in process.children(recursive=True):
                    child.terminate()

                process.terminate()

            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                psutil.ZombieProcess
            ):
                pass

        return f"Newest {app} instance closed."

    except Exception as e:
        return str(e)
'''    



#Old Code Logic
"""def open_brave():
    subprocess.Popen(["brave-browser-stable"])
    return "Brave opened successfully."


def open_vscode():
    subprocess.Popen(["code"])
    return "VS Code opened successfully."


def open_terminal():
    subprocess.Popen(["ptyxis"])
    return "Terminal opened successfully."


def open_apps(app):

    app = app.lower()

    if app not in Apps:
        return f"{app} is not supported."

    if is_running(Apps[app]["process"]):
        return f"{app} is already open."

    process = subprocess.Popen(Apps[app]["open"])

    opened_apps[app] = process

    return f"{app} opened successfully!"

def close_apps(app):

    app = app.lower()

    if app not in opened_apps:
        return f"{app} was not opened by Qubit."

    try:

        opened_apps[app].terminate()

        del opened_apps[app]

        return f"{app} terminated."

    except Exception as e:
        return str(e)

def close_apps(app):
    subprocess.run(["pkill", "-f", Apps[app]["close"]])
    return f"{app} terminated!"
"""