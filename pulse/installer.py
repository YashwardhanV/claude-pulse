"""Merge Claude Pulse hooks into ~/.claude/settings.json (and take them out again)."""
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .launcher import PULSE_PY

MARKER = "# claude-pulse"
SETTINGS_PATH = Path(os.environ.get("CLAUDE_SETTINGS_PATH", Path.home() / ".claude" / "settings.json"))

# Events that take a matcher get "*"; UserPromptSubmit and Stop ignore matchers, so none.
EVENTS = [
    ("UserPromptSubmit", False),
    ("PreToolUse", True),
    ("PostToolUse", True),
    ("PostToolUseFailure", True),
    ("PermissionRequest", True),
    ("Notification", True),
    ("Stop", False),
    ("SubagentStop", True),
    ("PreCompact", True),
    ("SessionEnd", True),
]
HOOK_TIMEOUT = 5


def git_bash_available():
    if os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        return True
    for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.exists(p):
            return True
    return bool(shutil.which("bash"))


def _exe(path):
    """A program path usable as the first word in both Git Bash and PowerShell."""
    p = str(path).replace("\\", "/")
    if " " not in p:
        return p
    return '"%s"' % p if git_bash_available() else '& "%s"' % p


def _arg(path):
    p = str(path).replace("\\", "/")
    return '"%s"' % p if " " in p else p


def curl_command(port):
    return ('curl.exe -s -m 1 --connect-timeout 0.3 -o NUL -H "Content-Type: application/json" '
            '--data-binary "@-" http://127.0.0.1:%d/event ; exit 0 %s' % (int(port), MARKER))


def ensure_command():
    return "%s %s ensure-running ; exit 0 %s" % (_exe(sys.executable), _arg(PULSE_PY), MARKER)


def our_hooks(port):
    hooks = {"SessionStart": [{"matcher": "*", "hooks": [
        {"type": "command", "command": ensure_command(), "timeout": HOOK_TIMEOUT}]}]}
    for event, has_matcher in EVENTS:
        group = {"hooks": [{"type": "command", "command": curl_command(port), "timeout": HOOK_TIMEOUT}]}
        if has_matcher:
            group = {"matcher": "*", **group}
        hooks[event] = [group]
    return hooks


def _is_ours(handler):
    return isinstance(handler, dict) and MARKER in str(handler.get("command", ""))


def strip(settings):
    """Remove only our handlers. Returns (new_settings, removed_count)."""
    out = copy.deepcopy(settings)
    hooks = out.get("hooks")
    removed = 0
    if not isinstance(hooks, dict):
        return out, 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept = [h for h in group["hooks"] if not _is_ours(h)]
            removed += len(group["hooks"]) - len(kept)
            if kept:
                kept_groups.append({**group, "hooks": kept})
            elif not any(_is_ours(h) for h in group["hooks"]):
                kept_groups.append(group)          # an empty group that was never ours
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        del out["hooks"]
    return out, removed


def merge(settings, port):
    """Add our hooks without touching anything else. Idempotent."""
    out, _ = strip(settings)
    hooks = out.setdefault("hooks", {})
    for event, groups in our_hooks(port).items():
        hooks.setdefault(event, [])
        hooks[event].extend(groups)
    return out


def load_settings(path=None):
    path = Path(path or SETTINGS_PATH)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text) if text.strip() else {}


def backup(path=None):
    path = Path(path or SETTINGS_PATH)
    if not path.exists():
        return None
    dest = path.with_name("settings.json.bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, dest)
    return dest


def write_settings(settings, path=None):
    path = Path(path or SETTINGS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install(port, path=None, pip=True, out=print):
    path = Path(path or SETTINGS_PATH)
    try:
        before = load_settings(path)
    except ValueError as e:
        out("ERROR: %s is not valid JSON (%s). Nothing was changed." % (path, e))
        return False
    bak = backup(path)
    after = merge(before, port)
    _, had = strip(before)
    write_settings(after, path)
    out("Settings: %s" % path)
    if bak:
        out("Backup:   %s" % bak)
    events = ["SessionStart"] + [e for e, _ in EVENTS]
    verb = "Updated" if had else "Added"
    out("%s Claude Pulse hooks for: %s" % (verb, ", ".join(events)))
    out("  hook command:  %s" % curl_command(port))
    out("  auto-start:    %s" % ensure_command())
    out("Everything else in settings.json was left as it was.")
    if pip:
        out("Installing Python dependencies (pywebview)...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pywebview"])
        out("  pip: %s" % ("ok" if r.returncode == 0 else "FAILED (exit %d)" % r.returncode))
    out("")
    out(">>> Restart Claude Code so the hooks load (hooks are read at session start).")
    out("    Then check them with /hooks. The Pulse window appears on the next session start.")
    return True


def uninstall(path=None, out=print):
    path = Path(path or SETTINGS_PATH)
    try:
        before = load_settings(path)
    except ValueError as e:
        out("ERROR: %s is not valid JSON (%s). Nothing was changed." % (path, e))
        return False
    after, removed = strip(before)
    if not removed:
        out("No Claude Pulse hooks found in %s - nothing to do." % path)
        return True
    bak = backup(path)
    write_settings(after, path)
    out("Removed %d Claude Pulse hook(s) from %s" % (removed, path))
    if bak:
        out("Backup: %s" % bak)
    out("Restart Claude Code for this to take effect. The server (if running) can be stopped with:")
    out("    python pulse.py stop")
    return True
