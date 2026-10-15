import copy
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pulse import installer

ROOT = Path(__file__).resolve().parent.parent
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"

USER_SETTINGS = {
    "permissions": {"defaultMode": "auto"},
    "model": "opus",
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-guard.sh"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
    },
}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_merge_adds_all_events_and_keeps_user_hooks():
    out = installer.merge(copy.deepcopy(USER_SETTINGS), 7777)
    assert out["permissions"] == {"defaultMode": "auto"} and out["model"] == "opus"
    expected = {"SessionStart"} | {e for e, _ in installer.EVENTS}
    assert expected <= set(out["hooks"])
    assert out["hooks"]["PreToolUse"][0] == USER_SETTINGS["hooks"]["PreToolUse"][0]
    assert out["hooks"]["Stop"][0] == USER_SETTINGS["hooks"]["Stop"][0]
    assert "matcher" not in out["hooks"]["Stop"][1]            # Stop has no matcher support
    assert out["hooks"]["PreToolUse"][1]["matcher"] == "*"
    for groups in out["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                if installer.MARKER in h["command"]:
                    assert h["timeout"] == installer.HOOK_TIMEOUT
                    assert h["command"].rstrip().endswith("exit 0 " + installer.MARKER)


def test_merge_is_idempotent():
    once = installer.merge(copy.deepcopy(USER_SETTINGS), 7777)
    twice = installer.merge(copy.deepcopy(once), 7777)
    assert once == twice


def test_merge_updates_port():
    out = installer.merge(installer.merge({}, 7777), 8888)
    cmds = [h["command"] for g in out["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert len(cmds) == 1 and "127.0.0.1:8888" in cmds[0]


def test_strip_restores_original_exactly():
    merged = installer.merge(copy.deepcopy(USER_SETTINGS), 7777)
    stripped, removed = installer.strip(merged)
    assert stripped == USER_SETTINGS
    assert removed == len(installer.EVENTS) + 1


def test_strip_keeps_foreign_handlers_in_shared_group():
    s = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "mine"},
        {"type": "command", "command": "curl ... " + installer.MARKER}]}]}}
    out, removed = installer.strip(s)
    assert removed == 1
    assert out == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}


def test_strip_on_empty_settings():
    assert installer.strip({}) == ({}, 0)
    out, removed = installer.strip(installer.merge({}, 7777))
    assert out == {} and removed == len(installer.EVENTS) + 1


def test_install_and_uninstall_on_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(USER_SETTINGS), encoding="utf-8")
    lines = []
    assert installer.install(7777, path=path, pip=False, out=lines.append)
    assert installer.install(7777, path=path, pip=False, out=lines.append)    # twice
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["hooks"]["PreToolUse"]) == 2
    assert list(tmp_path.glob("settings.json.bak-*"))
    assert installer.uninstall(path=path, out=lines.append)
    assert json.loads(path.read_text(encoding="utf-8")) == USER_SETTINGS


def test_install_refuses_invalid_json(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    assert not installer.install(7777, path=path, pip=False, out=lambda *_: None)
    assert path.read_text(encoding="utf-8") == "{ not json"


# ---- the real hook command must exit 0, quickly, even with no server ----------------------
PAYLOAD = json.dumps({"hook_event_name": "PreToolUse", "session_id": "x", "tool_name": "Bash",
                      "tool_input": {"command": "ls"}}).encode()


def run_hook(shell_argv, command):
    start = time.time()
    r = subprocess.run(shell_argv + [command], input=PAYLOAD, capture_output=True, timeout=20)
    return r, time.time() - start


@pytest.mark.skipif(not os.path.exists(GIT_BASH), reason="Git Bash not installed")
def test_hook_command_server_down_git_bash():
    r, took = run_hook([GIT_BASH, "-c"], installer.curl_command(free_port()))
    assert r.returncode == 0 and r.stdout == b""
    assert took < 1.5


@pytest.mark.skipif(not shutil.which("powershell"), reason="PowerShell not available")
def test_hook_command_server_down_powershell():
    r, took = run_hook(["powershell", "-NoProfile", "-Command"], installer.curl_command(free_port()))
    assert r.returncode == 0 and r.stdout.strip() == b""
    assert took < 3.0          # PowerShell's own startup dominates


@pytest.mark.skipif(not os.path.exists(GIT_BASH), reason="Git Bash not installed")
def test_hook_command_against_live_server(tmp_path):
    from pulse.server import Hub, PulseApp
    from pulse.state import Registry
    app = PulseApp({}, None, Hub())
    app.registry = Registry({}, emit=app.emit)
    port = app.start(port=0)
    try:
        r, took = run_hook([GIT_BASH, "-c"], installer.curl_command(port))
        assert r.returncode == 0 and r.stdout == b""
        for _ in range(50):
            if app.registry.get("x"):
                break
            time.sleep(0.02)
        assert app.registry.get("x").last_action == "Bash ls"
    finally:
        app.stop()


def test_ensure_running_starts_detached_server_and_replays_spool(tmp_path):
    """The SessionStart hook: server down -> spool payload, spawn server, exit 0 fast."""
    port = free_port()
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"port": port}), encoding="utf-8")
    env = dict(os.environ, CLAUDE_PULSE_HOME=str(home), PULSE_HEADLESS="1")
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "boot", "source": "startup",
                          "cwd": "C:\\code\\pagdandi"}).encode()
    start = time.time()
    r = subprocess.run([sys.executable, str(ROOT / "pulse.py"), "ensure-running"], input=payload,
                       env=env, capture_output=True, timeout=20)
    took = time.time() - start
    assert r.returncode == 0 and r.stdout == b""
    assert took < 2.0
    from pulse import launcher
    pid = None
    try:
        for _ in range(100):
            h = launcher.health(port)
            if h:
                pid = h["pid"]
                break
            time.sleep(0.1)
        assert pid, "server did not come up"
        state = None
        for _ in range(50):
            import urllib.request
            state = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/state" % port).read())
            if state["sessions"]:
                break
            time.sleep(0.1)
        assert [s["id"] for s in state["sessions"]] == ["boot"]
        # Second call while running: forwards directly, no second server.
        r2 = subprocess.run([sys.executable, str(ROOT / "pulse.py"), "ensure-running"],
                            input=payload.replace(b"boot", b"boot2"), env=env, capture_output=True, timeout=20)
        assert r2.returncode == 0
        assert launcher.health(port)["pid"] == pid
    finally:
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
