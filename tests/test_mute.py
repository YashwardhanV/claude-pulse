"""The x button mutes the live sessions; `autostart: false` stops hooks launching the server."""
import json
import socket

from pulse import config as config_mod
from pulse import launcher
from pulse.window import WindowController


class FakeWindow:
    def __init__(self):
        self.visible = True

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


def controller(live):
    ctl = WindowController({}, "http://x/")
    ctl.window = FakeWindow()
    ctl.hidden = False
    ctl._set_alpha = lambda alpha: None      # no Win32 window here
    ctl.active_ids_fn = lambda: set(live)
    return ctl


def test_x_keeps_window_hidden_through_more_events_from_the_same_session():
    live = {"a"}
    ctl = controller(live)
    ctl.hide()
    assert not ctl.window.visible
    for _ in range(5):                       # PreToolUse / PostToolUse / ... from session a
        ctl.sync(len(live), from_event=True)
    assert not ctl.window.visible and ctl.hidden


def test_new_session_brings_window_back_after_x():
    live = {"a"}
    ctl = controller(live)
    ctl.hide()
    live.add("b")
    ctl.sync(len(live), from_event=True)
    assert ctl.window.visible and not ctl.hidden


def test_muted_session_is_forgotten_once_it_ends():
    live = {"a"}
    ctl = controller(live)
    ctl.hide()
    live.clear()
    ctl.sync(0)
    assert ctl.muted == set()
    live.add("a")                            # same id reused later: not muted any more
    ctl.sync(1, from_event=True)
    assert ctl.window.visible


def test_without_x_events_show_the_window():
    live = {"a"}
    ctl = controller(live)
    ctl.window.visible, ctl.hidden = False, True
    ctl.sync(1, from_event=True)
    assert ctl.window.visible


def _closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_autostart_off_never_spawns_or_spools(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "HOME_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"port": _closed_port(), "autostart": False}))
    spawned = []
    monkeypatch.setattr(launcher, "spawn_server", lambda *a: spawned.append(a))
    assert launcher.ensure_running(b'{"hook_event_name":"SessionStart"}') == "autostart off"
    assert spawned == []
    assert not (tmp_path / "spool").exists()


def test_autostart_on_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "HOME_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"port": _closed_port()}))
    spawned = []
    monkeypatch.setattr(launcher, "spawn_server", lambda *a: spawned.append(a))
    assert launcher.ensure_running(b'{"hook_event_name":"SessionStart"}') == "started"
    assert len(spawned) == 1
