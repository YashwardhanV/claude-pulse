"""`pulse.py statusline`: context-window usage in Claude Code's own status line.

Claude Code pipes a JSON object on stdin every time the status line refreshes. It already
carries `context_window.used_percentage` and the real `context_window_size` (200k, or 1M for
extended-context models), so there is nothing to guess and no transcript to tail.
Docs: https://code.claude.com/docs/en/statusline
"""
import json
import sys

from .state import project_name

WIDTH = 10
RESET = "\033[0m"
DIM = "\033[2m"


def _color(pct):
    if pct > 90:
        return "\033[31m"      # red
    if pct > 75:
        return "\033[38;5;208m"  # orange
    if pct > 50:
        return "\033[33m"      # yellow
    return "\033[32m"          # green


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def render(data):
    """One line, e.g. 'pagdandi · Opus 5.5 · ctx ▓▓▓▓░░░░░░ 42%'. Never raises."""
    if not isinstance(data, dict):
        return ""
    ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    parts = [project_name(data.get("cwd") or ws.get("current_dir"))]
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    if model.get("display_name"):
        parts.append(str(model["display_name"]))

    cw = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
    pct = _num(cw.get("used_percentage"))
    if pct is None:
        parts.append(DIM + "ctx —" + RESET)       # before the first reply, and right after /compact
    else:
        pct = max(0.0, min(100.0, pct))
        filled = int(round(pct / 100 * WIDTH))
        bar = "▓" * filled + "░" * (WIDTH - filled)
        size = _num(cw.get("context_window_size"))
        of = " of 1M" if size and size >= 1_000_000 else ""
        text = "ctx %s %d%%%s" % (bar, round(pct), of)
        if pct > 90:
            text += " · consider /compact"
        parts.append(_color(pct) + text + RESET)
    return " · ".join(parts)


def main(stream=None, out=None):
    out = out or sys.stdout
    try:
        out.reconfigure(encoding="utf-8")       # Windows pipes default to cp1252: ▓ would fail
    except (AttributeError, ValueError):
        pass
    try:
        raw = (stream or sys.stdin).read()
        line = render(json.loads(raw) if raw.strip() else {})
    except Exception:
        line = ""
    out.write(line + "\n")
    out.flush()
    return 0
