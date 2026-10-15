"""pywebview window: frameless, always on top, docked to the right edge."""
import ctypes
import logging
import os
import sys
import threading

from . import config as config_mod

log = logging.getLogger("pulse")

WIDTH = 220
TITLE = "Claude Pulse"
HOVER_ALPHA = 0.35


class Api:
    """Exposed to the page as window.pywebview.api."""

    def __init__(self, ctl):
        self._ctl = ctl

    def fit(self, height):
        self._ctl.fit(height)

    def hide(self):
        self._ctl.hide()

    def hover(self, on):
        self._ctl.hover(bool(on))


class WindowController:
    def __init__(self, cfg, url, title=TITLE, remember_position=True):
        self.cfg = cfg
        self.url = url
        self.title = title
        self.remember = remember_position
        self.window = None
        self.hidden = False
        self._height = 240
        self._save_timer = None
        self._hwnd = None
        self.screen = (1920, 1080)

    # -- creation ----------------------------------------------------------------------
    def create(self):
        import webview
        try:
            scr = webview.screens[0]
            self.screen = (scr.width, scr.height)
        except Exception:
            pass
        wcfg = self.cfg.get("window", {})
        x, y = wcfg.get("x"), wcfg.get("y")
        if x is None or y is None or not self._on_screen(x, y):
            x, y = self.screen[0] - WIDTH - 10, 70
        self.window = webview.create_window(
            self.title, self.url, js_api=Api(self), width=WIDTH, height=self._height,
            x=int(x), y=int(y), frameless=True, on_top=True, resizable=False, easy_drag=False,
            background_color="#0b0d12", min_size=(WIDTH, 60), focus=False)
        self.window.events.moved += self._on_moved
        self.window.events.shown += self._on_shown
        return self.window

    def run(self):
        import webview
        webview.start(debug=bool(os.environ.get("PULSE_DEBUG")))

    def _on_screen(self, x, y):
        return -WIDTH < x < self.screen[0] + 4000 and -50 < y < self.screen[1] + 2000

    def _on_shown(self):
        threading.Timer(0.3, lambda: self._set_alpha(self._opacity())).start()

    # -- page callbacks ----------------------------------------------------------------
    def fit(self, height):
        h = max(60, min(int(height), self.screen[1] - 60))
        if h == self._height or not self.window:
            return
        self._height = h
        self.window.resize(WIDTH, h)

    def hide(self):
        if self.window and not self.hidden:
            self.hidden = True
            self.window.hide()

    def show_if_hidden(self):
        if self.window and self.hidden:
            self.hidden = False
            self.window.show()
            threading.Timer(0.2, lambda: self._set_alpha(self._opacity())).start()

    def hover(self, on):
        self._set_alpha(HOVER_ALPHA if on else self._opacity())

    def _opacity(self):
        try:
            return max(0.2, min(1.0, float(self.cfg.get("window", {}).get("opacity", 0.95))))
        except (TypeError, ValueError):
            return 0.95

    def _on_moved(self, x, y):
        if not self.remember:
            return
        if self._save_timer:
            self._save_timer.cancel()

        def save():
            def mut(c):
                c["window"]["x"], c["window"]["y"] = int(x), int(y)
            try:
                config_mod.update(mut)
                self.cfg.setdefault("window", {}).update(x=int(x), y=int(y))
            except Exception:
                log.exception("saving window position failed")
        self._save_timer = threading.Timer(0.8, save)
        self._save_timer.start()

    # -- Win32 window alpha ------------------------------------------------------------
    def _find_hwnd(self):
        if sys.platform != "win32":
            return None
        if self._hwnd:
            return self._hwnd
        user32 = ctypes.windll.user32
        pid = os.getpid()
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(hwnd, _):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
            if owner.value == pid:
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 256)
                if buf.value == self.title:
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(cb, 0)
        self._hwnd = found[0] if found else None
        return self._hwnd

    def _set_alpha(self, alpha):
        hwnd = self._find_hwnd()
        if not hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            GWL_EXSTYLE, WS_EX_LAYERED, LWA_ALPHA = -20, 0x00080000, 0x2
            h = ctypes.c_void_p(hwnd)
            style = user32.GetWindowLongW(h, GWL_EXSTYLE)
            if not style & WS_EX_LAYERED:
                user32.SetWindowLongW(h, GWL_EXSTYLE, style | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(h, 0, int(alpha * 255), LWA_ALPHA)
        except Exception:
            log.exception("set alpha failed")
