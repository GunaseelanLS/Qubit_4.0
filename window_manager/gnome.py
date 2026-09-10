import subprocess
import re
import ast
from .base import WindowManager
from .resolve import expand

class GNOMEWindowManager(WindowManager):
    DEST = "org.qubit.WindowManager"
    PATH = "/org/qubit/WindowManager"

    def __init__(self):
        self._status = self._detect_status_interface()

    def _detect_status_interface(self):
        try:
            xml = subprocess.run(
                ["gdbus", "introspect", "--session",
                 "--dest", self.DEST,
                 "--object-path", self.PATH],
                capture_output=True,
                text=True
            ).stdout

            return re.search(
                r"FocusWindow\(\s*in\s+s\s+\w+\s*,\s*out\s+b\s+\w+\s*\)",
                xml
            ) is not None
        except Exception:
            return False

    def _call(self, method, app=None):
        cmd = [
            "gdbus", "call",
            "--session",
            "--dest", self.DEST,
            "--object-path", self.PATH,
            "--method", f"org.qubit.WindowManager.{method}"
        ]

        if app is not None:
            cmd.append(app)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )

        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()

    def _call_bool(self, method, app):
        success, output, error = self._call(method, app)

        if not success:
            return None

        if not self._status:
            return True

        try:
            values = ast.literal_eval(
                output.replace("true", "True").replace("false", "False")
            )
        except (ValueError, SyntaxError):
            return None

        if isinstance(values, bool):
            return values

        if isinstance(values, tuple) and values and isinstance(values[0], bool):
            return values[0]

        return None

    def _parse_strings(self, output):
        try:
            values = ast.literal_eval(output)
            if isinstance(values, tuple) and len(values) == 3 and all(isinstance(v, str) for v in values):
                return list(values)
        except (ValueError, SyntaxError):
            pass

        match = re.search(
            r"\('([^']*)', '([^']*)', '([^']*)'\)",
            output
        )

        if not match:
            return None

        return [match.group(1), match.group(2), match.group(3)]

    def _try_targets(self, method, app):
        for cand in expand(app):
            found = self._call_bool(method, cand)

            if found is True:
                return True

        return False

    def get_active_window(self):
        success, output, error = self._call("GetActiveWindow")

        if not success:
            return None

        values = self._parse_strings(output)

        if not values:
            return None

        return {
            "app": values[0],
            "caption": values[1],
            "uuid": values[2]
        }

    def focus_window(self, app):
        found = self._try_targets("FocusWindow", app)

        if found:
            return f"Focused {app}"

        return f"No window found for {app}"

    def close_window(self, app):
        found = self._try_targets("CloseWindow", app)

        if found:
            return f"Closed {app}"

        return f"Could not close {app}"

    def minimize_window(self, app):
        found = self._try_targets("MinimizeWindow", app)

        if found:
            return f"Minimized {app}"

        return f"No window found for {app}"

    def maximize_window(self, app):
        found = self._try_targets("MaximizeWindow", app)

        if found:
            return f"Maximized {app}"

        return f"No window found for {app}"

    def close_active_window(self):
        active = self.get_active_window()

        if not active or not active["app"]:
            return "No active window found."

        return self.close_window(active["uuid"] or active["app"])