import io
import json
import subprocess
import sys
from pathlib import Path

from pulse.statusline import render

ROOT = Path(__file__).resolve().parent.parent


def plain(text):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_render_uses_claude_codes_own_numbers():
    line = plain(render({"cwd": "C:\\code\\pagdandi", "model": {"display_name": "Opus 5.5"},
                         "context_window": {"used_percentage": 42.4, "context_window_size": 200000}}))
    assert line == "pagdandi · Opus 5.5 · ctx ▓▓▓▓░░░░░░ 42%"


def test_render_1m_window_and_compact_hint():
    line = plain(render({"cwd": "/x/drivedex",
                         "context_window": {"used_percentage": 93, "context_window_size": 1000000}}))
    assert line == "drivedex · ctx ▓▓▓▓▓▓▓▓▓░ 93% of 1M · consider /compact"


def test_render_before_first_reply_and_bad_input():
    assert plain(render({"cwd": "/x/p", "context_window": {"used_percentage": None}})) == "p · ctx —"
    assert render("nope") == ""
    assert plain(render({})) == "claude · ctx —"


def test_colors_by_level():
    assert "\x1b[32m" in render({"context_window": {"used_percentage": 10}})
    assert "\x1b[31m" in render({"context_window": {"used_percentage": 95}})


def test_command_end_to_end_never_fails():
    payload = json.dumps({"cwd": "/x/pagdandi", "context_window": {"used_percentage": 50}}).encode()
    r = subprocess.run([sys.executable, str(ROOT / "pulse.py"), "statusline"], input=payload,
                       capture_output=True, timeout=20)
    assert r.returncode == 0 and "50%" in r.stdout.decode("utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "pulse.py"), "statusline"], input=b"garbage",
                       capture_output=True, timeout=20)
    assert r.returncode == 0 and r.stdout.strip() == b""
