"""Config file handling: ~/.claude-pulse/config.json."""
import copy
import json
import os
import secrets
import string
import threading
from pathlib import Path

HOME_DIR = Path(os.environ.get("CLAUDE_PULSE_HOME", Path.home() / ".claude-pulse"))
CONFIG_PATH = HOME_DIR / "config.json"

DEFAULTS = {
    "port": 7777,
    "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": ""},
    "notify_blocked_after_seconds": 30,
    "notify_done_if_session_ran_minutes": 2,
    "thinking_silence_seconds": 5,
    "context_window_tokens": 200000,
    "context_window_overrides": {},
    "window": {"x": None, "y": None, "opacity": 0.95},
}

_lock = threading.Lock()


def new_topic(length=24):
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path=None):
    """Load config, creating it with defaults (and a fresh ntfy topic) on first run."""
    path = Path(path or CONFIG_PATH)
    with _lock:
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        cfg = _merge(DEFAULTS, data)
        if not cfg["ntfy"].get("topic"):
            cfg["ntfy"]["topic"] = new_topic()
        if cfg != data:
            _write(path, cfg)
        return cfg


def save(cfg, path=None):
    with _lock:
        _write(Path(path or CONFIG_PATH), cfg)


def update(mutator, path=None):
    """Re-read the file, apply mutator(cfg) and write it back."""
    cfg = load(path)
    mutator(cfg)
    save(cfg, path)
    return cfg


def _write(path, cfg):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, path)
