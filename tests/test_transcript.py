import json

from pulse.state import Registry
from pulse.transcript import FileTail, TranscriptWatcher, parse_assistant, usage_tokens

CFG = {"thinking_silence_seconds": 5, "context_window_tokens": 200000}


def assistant(content_types, usage=None, sidechain=False):
    msg = {"role": "assistant", "content": [{"type": t, t if t != "tool_use" else "name": "x"}
                                            for t in content_types]}
    if usage:
        msg["usage"] = usage
    return {"type": "assistant", "isSidechain": sidechain, "message": msg}


USAGE = {"input_tokens": 2, "cache_creation_input_tokens": 2942, "cache_read_input_tokens": 62730,
         "output_tokens": 683}


def write_lines(path, entries, mode="a", partial=None):
    with open(path, mode, encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
        if partial is not None:
            f.write(partial)


def test_usage_tokens_matches_status_line_formula():
    assert usage_tokens(USAGE) == 2 + 2942 + 62730       # output tokens excluded
    assert usage_tokens({}) is None
    assert usage_tokens(None) is None


def test_parse_assistant():
    assert parse_assistant(assistant(["thinking"], USAGE)) == (65674, True, False)
    assert parse_assistant(assistant(["tool_use"])) == (None, False, True)
    assert parse_assistant(assistant(["thinking"], USAGE, sidechain=True)) is None
    assert parse_assistant({"type": "user", "message": {}}) is None


def test_file_tail_handles_partial_last_line(tmp_path):
    p = tmp_path / "t.jsonl"
    full = json.dumps(assistant(["text"], USAGE))
    write_lines(p, [{"type": "user"}], mode="w", partial=full[:20])
    tail = FileTail(str(p))
    assert [e["type"] for e in tail.read_lines()] == ["user"]
    with open(p, "a", encoding="utf-8") as f:
        f.write(full[20:] + "\n")
    assert [e["type"] for e in tail.read_lines()] == ["assistant"]
    assert tail.read_lines() == []


def test_file_tail_skips_corrupt_lines_and_truncation(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"type":"user"}\nnot json\n{"type":"x"}\n', encoding="utf-8")
    tail = FileTail(str(p))
    assert [e["type"] for e in tail.read_lines()] == ["user", "x"]
    p.write_text('{"type":"new"}\n', encoding="utf-8")       # replaced by a shorter file
    assert [e["type"] for e in tail.read_lines()] == ["new"]


def test_file_tail_starts_near_end_of_big_files(tmp_path):
    p = tmp_path / "big.jsonl"
    write_lines(p, [{"type": "user", "pad": "x" * 500}] * 200, mode="w")
    write_lines(p, [{"type": "last"}])
    tail = FileTail(str(p), initial_tail=2000)
    lines = tail.read_lines()
    assert lines[-1]["type"] == "last" and len(lines) < 10


def make(tmp_path):
    reg = Registry(dict(CFG), clock=lambda: 1000.0)
    p = tmp_path / "s1.jsonl"
    write_lines(p, [assistant(["text"], USAGE)], mode="w")
    reg.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/x/proj",
                "transcript_path": str(p)})
    return reg, TranscriptWatcher(reg), p


def test_watcher_reads_context_on_attach(tmp_path):
    reg, w, p = make(tmp_path)
    w.poll()
    s = reg.get("s1")
    assert s.context_tokens == 65674 and s.context_pct == round(65674 / 2000, 1)
    assert s.base == "working"        # initial scan never claims "thinking"


def test_watcher_confirms_thinking_from_new_lines(tmp_path):
    reg, w, p = make(tmp_path)
    w.poll()
    write_lines(p, [assistant(["thinking"], {"input_tokens": 1, "cache_read_input_tokens": 99999})])
    w.poll()
    s = reg.get("s1")
    assert s.base == "thinking"
    assert s.context_tokens == 100000 and s.context_pct == 50.0


def test_thinking_followed_by_tool_use_is_not_thinking(tmp_path):
    reg, w, p = make(tmp_path)
    w.poll()
    write_lines(p, [assistant(["thinking"]), assistant(["tool_use"])])
    w.poll()
    assert reg.get("s1").base == "working"


def test_watcher_ignores_sidechain_usage(tmp_path):
    reg, w, p = make(tmp_path)
    w.poll()
    write_lines(p, [assistant(["text"], {"input_tokens": 150000}, sidechain=True)])
    w.poll()
    assert reg.get("s1").context_tokens == 65674


def test_precompact_then_next_reading(tmp_path):
    reg, w, p = make(tmp_path)
    w.poll()
    reg.handle({"hook_event_name": "PreCompact", "session_id": "s1", "trigger": "auto"})
    w.poll()                                   # nothing new: stays reset
    assert reg.get("s1").context_pct is None
    write_lines(p, [assistant(["text"], {"input_tokens": 20000})])
    w.poll()
    assert reg.get("s1").context_pct == 10.0


def test_missing_transcript_is_harmless(tmp_path):
    reg = Registry(dict(CFG), clock=lambda: 1000.0)
    reg.handle({"hook_event_name": "SessionStart", "session_id": "s9",
                "transcript_path": str(tmp_path / "nope.jsonl")})
    TranscriptWatcher(reg).poll()
    assert reg.get("s9").context_pct is None
