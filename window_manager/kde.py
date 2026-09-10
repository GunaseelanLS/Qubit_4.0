from .base import WindowManager
from .resolve import expand
import subprocess
import re
import os
import tempfile
import time


class KDEWindowManager(WindowManager):

    def get_active_window(self):
        result = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.kde.KWin",
             "--object-path", "/KWin",
             "--method", "org.kde.KWin.queryWindowInfo"],
            capture_output=True,
            text=True
        )

        output = result.stdout

        caption = re.search(r"'caption': <'([^']*)'>", output)
        app = re.search(r"'resourceClass': <'([^']*)'>", output)
        uuid = re.search(r"'uuid': <'([^']*)'>", output)

        return {
            "caption": caption.group(1) if caption else None,
            "app": app.group(1) if app else None,
            "uuid": uuid.group(1) if uuid else None
        }

    def close_active_window(self):
        result = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.kde.KWin",
             "--object-path", "/KWin",
             "--method", "org.kde.KWin.killWindow"],
            capture_output=True,
            text=True
        )

        return (
            "Active window closed."
            if result.returncode == 0
            else "Failed to close active window."
        )

    def _candidates(self, app):
        return expand(app)

    def _matches(self, app, active):
        app_name = (active.get("app") or "").lower()
        caption = (active.get("caption") or "").lower()

        for cand in self._candidates(app):
            if cand and (cand in app_name or cand in caption):
                return True

        return False

    def focus_window(self, app):
        match = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.kde.KWin",
             "--object-path", "/WindowsRunner",
             "--method", "org.kde.krunner1.Match", app],
            capture_output=True,
            text=True
        )

        match_id = re.search(r"'([^']+)'", match.stdout)

        if not match_id:
            return f"No window found for {app}"

        result = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.kde.KWin",
             "--object-path", "/WindowsRunner",
             "--method", "org.kde.krunner1.Run",
             match_id.group(1), ""],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return f"Could not focus {app}"

        for _ in range(6):
            active = self.get_active_window()

            if active and self._matches(app, active):
                return f"Focused {app}"

            time.sleep(0.25)

        return f"Could not focus {app}"

    def close_window(self, app):
        result = self.focus_window(app)

        if result.startswith("Could not focus") or result.startswith("No window found"):
            return result

        active = self.get_active_window()

        if not active or not active.get("app"):
            return f"Could not verify {app}"

        if not self._matches(app, active):
            return f"Could not focus {app}"

        ret = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.kde.KWin",
             "--object-path", "/KWin",
             "--method", "org.kde.KWin.killWindow"],
            capture_output=True,
            text=True
        )

        if ret.returncode != 0:
            return f"Could not close {app}"

        return f"Closed {app}"

    def _run_kwin_script(self, script):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name

        try:
            for dest in ("org.kde.kwin", "org.kde.KWin"):
                load = subprocess.run(
                    ["gdbus", "call", "--session",
                     "--dest", dest,
                     "--object-path", "/Scripting",
                     "--method", "org.kde.kwin.Scripting.loadScript", path],
                    capture_output=True,
                    text=True
                )

                if load.returncode != 0:
                    continue

                sid = re.search(r"'([^']+)'", load.stdout)

                if not sid:
                    continue

                start = subprocess.run(
                    ["gdbus", "call", "--session",
                     "--dest", dest,
                     "--object-path", "/Scripting",
                     "--method", "org.kde.kwin.Scripting.start", sid.group(1)],
                    capture_output=True,
                    text=True
                )

                time.sleep(0.5)

                subprocess.run(
                    ["gdbus", "call", "--session",
                     "--dest", dest,
                     "--object-path", "/Scripting",
                     "--method", "org.kde.kwin.Scripting.unloadScript", sid.group(1)],
                    capture_output=True,
                    text=True
                )

                return start.returncode == 0
        finally:
            os.unlink(path)

        return False

    def minimize_window(self, app):
        result = self.focus_window(app)

        if result.startswith("Could not focus") or result.startswith("No window found"):
            return result

        if not self._run_kwin_script(
            "var w = workspace.activeWindow; if (w) w.minimized = true;"
        ):
            return f"Could not minimize {app}"

        return f"Minimized {app}"

    def maximize_window(self, app):
        result = self.focus_window(app)

        if result.startswith("Could not focus") or result.startswith("No window found"):
            return result

        active = self.get_active_window()

        if not active or not active.get("app"):
            return f"Could not verify {app}"

        if not self._matches(app, active):
            return f"Could not focus {app}"

        ret = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.kde.KWin",
             "--object-path", "/KWin",
             "--method", "org.kde.KWin.toggleMaximize"],
            capture_output=True,
            text=True
        )

        if ret.returncode != 0:
            return f"Could not maximize {app}"

        return f"Maximized {app}"