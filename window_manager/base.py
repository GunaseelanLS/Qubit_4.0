class WindowManager:

    def get_active_window(self):
        raise NotImplementedError

    def close_active_window(self):
        raise NotImplementedError

    def focus_window(self, app):
        raise NotImplementedError

    def close_window(self, app):
        raise NotImplementedError

    def minimize_window(self, app):
        raise NotImplementedError

    def maximize_window(self, app):
        raise NotImplementedError