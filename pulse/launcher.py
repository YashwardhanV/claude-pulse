"""Health checks, auto-start (`ensure-running`) and the single-instance lock."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from . import config as config_mod

ROOT = Path(__file__).resolve().parent.parent
PULSE_PY = ROOT / "pulse.py"
START_LOCK_SECONDS = 15


def pid_path():
    return config_mod.HOME_DIR / "pulse.pid"


def start_lock_path():
    return config_mod.HOME_DIR / "starting.lock"


def port_open(port, timeout=0.25):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def health(port, timeout=0.5):
    """Returns the /health dict if a Claude Pulse server answers on this port, else None."""
    if not port_open(port, timeout=min(timeout, 0.25)):
        return None
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % int(port), timeout=timeout) as r:
            data = json.loads(r.read().decode())
            return data if data.get("app") == "claude-pulse" else None
    except Exception:
        return None


def post_event(port, body, timeout=0.5):
    if not port_open(port):
        return False
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/event" % int(port), data=body,
                                     method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 300
    except Exception:
        return False


def spool(body):
    d = config_mod.HOME_DIR / "spool"
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%d-%s.json" % (time.time() * 1000, uuid.uuid4().hex[:6]))).write_bytes(body)


def acquire_start_lock():
    """Only one launcher may spawn the server at a time."""
    path = start_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - path.stat().st_mtime > START_LOCK_SECONDS:
            path.unlink()
    except OSError:
        pass
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except OSError:
        return False


def release_start_lock():
    try:
        start_lock_path().unlink()
    except OSError:
        pass


def pythonw():
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)


def spawn_server(extra_args=()):
    """Start `pulse.py serve` fully detached from the calling terminal."""
    args = [pythonw(), str(PULSE_PY), "serve", *extra_args]
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True, cwd=str(ROOT))
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            return subprocess.Popen(args, creationflags=base | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:      # the job we're in doesn't allow breakaway
            return subprocess.Popen(args, creationflags=base, **kwargs)
    return subprocess.Popen(args, start_new_session=True, **kwargs)


def read_stdin(limit=8 * 1024 * 1024):
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return b""
        return sys.stdin.buffer.read(limit)
    except Exception:
        return b""


def ensure_running(body=b""):
    """SessionStart hook: forward the payload, starting the server if it isn't up."""
    cfg = config_mod.load()
    port = int(cfg.get("port", 7777))
    if body and post_event(port, body):
        return "forwarded"
    if health(port):
        return "running"
    if body:
        spool(body)          # the server replays this when it comes up
    if port_open(port):
        return "port %d busy (not Claude Pulse)" % port
    if acquire_start_lock():
        spawn_server()
        return "started"
    return "starting"
