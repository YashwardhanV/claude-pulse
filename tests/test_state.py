import pytest

from pulse import state as st
from pulse.state import Registry
from pulse.summarize import response_failed, short_tool, summarize


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


CFG = {
    "thinking_silence_seconds": 5,
    "notify_blocked_after_seconds": 30,
    "notify_done_if_session_ran_minutes": 2,
    "context_window_tokens": 200000,
}


@pytest.fixture
def env():
    clock = Clock()
    emitted, notified = [], []
    reg = Registry(dict(CFG), clock=clock,
                   emit=lambda kind, data: emitted.append((kind, data)),
                   notify=lambda kind, s: notified.append((kind, s.project)))
    return reg, clock, emitted, notified


def ev(name, sid="s1", cwd="C:\\code\\pagdandi", **kw):
    return dict(hook_event_name=name, session_id=sid, cwd=cwd,
                transcript_path="C:\\t\\%s.jsonl" % sid, **kw)


def pulses(emitted):
    return [d for k, d in emitted if k == "pulse"]


def test_session_start_is_idle(env):
    reg, clock, _, _ = env
    reg.handle(ev("SessionStart", source="startup"))
    assert reg.state_of("s1") == "idle"
    assert reg.get("s1").project == "pagdandi"


def test_unknown_session_is_created_lazily(env):
    reg, *_ = env
    reg.handle(ev("PreToolUse", sid="late", tool_name="Read", tool_input={"file_path": "/a/b.py"}))
    assert reg.state_of("late") == "working"


def test_payload_without_session_id_is_ignored(env):
    reg, *_ = env
    assert reg.handle({"hook_event_name": "Stop"}) is None
    assert reg.handle("not a dict") is None
    assert reg.sessions == {}


def test_prompt_then_silence_becomes_thinking(env):
    reg, clock, _, _ = env
    reg.handle(ev("SessionStart"))
    reg.handle(ev("UserPromptSubmit", prompt="secret prompt"))
    assert reg.state_of("s1") == "working"
    clock.advance(4.9)
    reg.tick()
    assert reg.state_of("s1") == "working"
    clock.advance(0.2)
    reg.tick()
    assert reg.state_of("s1") == "thinking"


def test_tool_events_keep_working_and_reset_silence(env):
    reg, clock, emitted, _ = env
    reg.handle(ev("UserPromptSubmit"))
    for _ in range(3):
        clock.advance(4)
        reg.handle(ev("PreToolUse", tool_name="Edit", tool_input={"file_path": "C:\\x\\app.py", "old_string": "SECRET"}))
        reg.tick()
        assert reg.state_of("s1") == "working"
    s = reg.get("s1")
    assert s.last_action == "Edit app.py"
    assert "SECRET" not in repr(reg.snapshot())
    assert [p["label"] for p in pulses(emitted)] == ["Edit", "Edit", "Edit"]
    clock.advance(5)
    reg.tick()
    assert reg.state_of("s1") == "thinking"
    reg.handle(ev("PostToolUse", tool_name="Edit", tool_response={"filePath": "x"}))
    assert reg.state_of("s1") == "working"


def test_thinking_timeout_is_configurable(env):
    reg, clock, _, _ = env
    reg.cfg["thinking_silence_seconds"] = 2
    reg.handle(ev("UserPromptSubmit"))
    clock.advance(2.1)
    reg.tick()
    assert reg.state_of("s1") == "thinking"


def test_transcript_thinking_confirmation(env):
    reg, clock, _, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.transcript_thinking("s1")
    assert reg.state_of("s1") == "thinking"
    reg.handle(ev("Stop"))
    reg.transcript_thinking("s1")          # never overrides done
    assert reg.state_of("s1") == "done"


def test_failure_event_is_error_then_reverts_after_6s(env):
    reg, clock, emitted, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("PreToolUse", tool_name="Bash", tool_input={"command": "npm test"}))
    reg.handle(ev("PostToolUseFailure", tool_name="Bash", error="Exit code 1\nboom"))
    assert reg.state_of("s1") == "error"
    assert any(p["kind"] == "error" for p in pulses(emitted))
    clock.advance(3)
    reg.handle(ev("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"}))
    assert reg.state_of("s1") == "error"   # overlay holds for the full 6s
    clock.advance(3.1)
    reg.tick()
    assert reg.state_of("s1") == "working"


def test_error_reverts_to_done_if_stop_arrived_meanwhile(env):
    reg, clock, _, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("PostToolUseFailure", tool_name="Bash", error="Exit code 2"))
    reg.handle(ev("Stop"))
    assert reg.state_of("s1") == "error"
    clock.advance(6)
    reg.tick()
    assert reg.state_of("s1") == "done"


def test_post_tool_use_error_detection(env):
    reg, clock, _, _ = env
    reg.handle(ev("PostToolUse", tool_name="Bash", tool_response={"stdout": "", "exitCode": 1}))
    assert reg.state_of("s1") == "error"
    clock.advance(7)
    reg.tick()
    reg.handle(ev("PostToolUse", tool_name="Bash", tool_response={"stdout": "ok", "stderr": "warn"}))
    assert reg.state_of("s1") == "working"


def test_interrupt_is_not_an_error(env):
    reg, *_ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("PostToolUseFailure", tool_name="Bash", error="Interrupted", is_interrupt=True))
    assert reg.state_of("s1") == "done"
    assert reg.get("s1").last_action == "interrupted"


def test_blocked_by_notification_then_resumes(env):
    reg, clock, _, notified = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("Notification", notification_type="permission_prompt", message="needs permission"))
    assert reg.state_of("s1") == "blocked"
    snap = reg.snapshot()["sessions"][0]
    assert snap["status"] == "blocked · permission needed"
    clock.advance(10)
    reg.handle(ev("PostToolUse", tool_name="Bash", tool_response={}))
    assert reg.state_of("s1") == "working"
    clock.advance(60)
    reg.tick()
    assert notified == []                 # answered quickly: no phone ping


def test_blocked_beats_error_and_silence(env):
    reg, clock, _, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("PostToolUseFailure", tool_name="Bash", error="Exit code 1"))
    reg.handle(ev("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf x"}))
    assert reg.state_of("s1") == "blocked"
    clock.advance(20)
    reg.tick()
    assert reg.state_of("s1") == "blocked"
    assert reg.snapshot()["sessions"][0]["status"] == "blocked · permission for Bash"


def test_permission_prompt_keeps_better_reason(env):
    reg, *_ = env
    reg.handle(ev("PermissionRequest", tool_name="Write"))
    reg.handle(ev("Notification", notification_type="permission_prompt"))
    assert reg.get("s1").blocked_reason == "permission for Write"


def test_ask_user_question_blocks(env):
    reg, *_ = env
    reg.handle(ev("PreToolUse", tool_name="AskUserQuestion", tool_input={"questions": []}))
    assert reg.state_of("s1") == "blocked"


def test_idle_prompt_after_stop_stays_done(env):
    reg, *_ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("Stop"))
    reg.handle(ev("Notification", notification_type="idle_prompt"))
    assert reg.state_of("s1") == "done"


def test_idle_prompt_without_stop_blocks(env):
    reg, *_ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("Notification", notification_type="idle_prompt"))
    assert reg.state_of("s1") == "blocked"


def test_blocked_notification_after_threshold_once(env):
    reg, clock, _, notified = env
    reg.handle(ev("PermissionRequest", tool_name="Bash"))
    clock.advance(29)
    reg.tick()
    assert notified == []
    clock.advance(1.5)
    reg.tick()
    reg.tick()
    clock.advance(30)
    reg.tick()
    assert notified == [("blocked", "pagdandi")]
    # A second, separate blocked episode notifies again.
    reg.handle(ev("PostToolUse", tool_name="Bash"))
    reg.handle(ev("PermissionRequest", tool_name="Bash"))
    clock.advance(31)
    reg.tick()
    assert notified == [("blocked", "pagdandi")] * 2


def test_done_notification_only_for_long_tasks(env):
    reg, clock, _, notified = env
    reg.handle(ev("UserPromptSubmit"))
    clock.advance(60)
    reg.handle(ev("Stop"))
    assert reg.state_of("s1") == "done"
    assert notified == []
    reg.handle(ev("UserPromptSubmit"))
    clock.advance(121)
    reg.handle(ev("Stop"))
    assert notified == [("done", "pagdandi")]
    reg.handle(ev("Stop"))                # no task running: no duplicate
    assert notified == [("done", "pagdandi")]


def test_subagent_stop_flutters_without_state_change(env):
    reg, _, emitted, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("SubagentStop", agent_type="Explore"))
    assert reg.state_of("s1") == "working"
    assert pulses(emitted)[-1]["kind"] == "flutter"


def test_precompact_breath_and_context_reset(env):
    reg, _, emitted, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.set_context("s1", 190000)
    assert reg.get("s1").context_pct == 95.0
    reg.handle(ev("PreCompact", trigger="auto"))
    assert reg.get("s1").context_pct is None
    assert pulses(emitted)[-1]["kind"] == "breath"
    reg.set_context("s1", 30000)
    assert reg.get("s1").context_pct == 15.0


def test_context_above_window_assumes_1m(env):
    reg, *_ = env
    reg.handle(ev("SessionStart"))
    reg.set_context("s1", 515000)
    s = reg.get("s1")
    assert s.context_window == 1_000_000 and s.context_pct == 51.5


def test_1m_model_id_sets_window(env):
    reg, *_ = env
    reg.handle(ev("SessionStart", model="claude-opus-5-5[1m]"))
    reg.set_context("s1", 190000)
    assert reg.get("s1").context_pct == 19.0


def test_session_end_then_removed_after_10s(env):
    reg, clock, _, _ = env
    reg.handle(ev("SessionStart"))
    reg.handle(ev("SessionEnd", reason="prompt_input_exit"))
    assert reg.state_of("s1") == "ended"
    clock.advance(9)
    reg.tick()
    assert "s1" in reg.sessions
    clock.advance(1.1)
    reg.tick()
    assert "s1" not in reg.sessions


def test_session_end_for_unknown_session_is_ignored(env):
    reg, *_ = env
    reg.handle(ev("SessionEnd", sid="ghost"))
    assert reg.sessions == {}


def test_stale_session_cleanup(env):
    reg, clock, _, _ = env
    reg.handle(ev("SessionStart"))
    reg.handle(ev("Stop"))
    clock.advance(st.STALE_SECONDS - 1)
    reg.tick()
    assert reg.state_of("s1") == "done"
    clock.advance(2)
    reg.tick()
    assert reg.state_of("s1") == "ended"
    clock.advance(11)
    reg.tick()
    assert reg.sessions == {}


def test_blocked_session_survives_normal_stale_limit(env):
    reg, clock, _, _ = env
    reg.handle(ev("PermissionRequest", tool_name="Bash"))
    clock.advance(st.STALE_SECONDS + 5)
    reg.tick()
    assert reg.state_of("s1") == "blocked"
    clock.advance(st.STALE_BLOCKED_SECONDS)
    reg.tick()
    assert reg.state_of("s1") == "ended"


def test_event_after_end_starts_fresh_session(env):
    reg, clock, _, _ = env
    reg.handle(ev("SessionStart"))
    reg.handle(ev("SessionEnd"))
    reg.handle(ev("UserPromptSubmit"))
    assert reg.state_of("s1") == "working"


def test_multi_session_titles_and_blocked_first(env):
    reg, clock, _, _ = env
    reg.handle(ev("SessionStart", sid="aaaa1111", cwd="/x/pagdandi"))
    clock.advance(1)
    reg.handle(ev("SessionStart", sid="bbbb2222", cwd="/x/pagdandi"))
    clock.advance(1)
    reg.handle(ev("SessionStart", sid="cccc3333", cwd="/x/drivedex"))
    reg.handle(ev("PermissionRequest", sid="cccc3333", cwd="/x/drivedex", tool_name="Bash"))
    snap = reg.snapshot()["sessions"]
    assert [s["title"] for s in snap] == ["drivedex", "pagdandi · aaaa", "pagdandi · bbbb"]
    assert snap[0]["state"] == "blocked"


def test_event_log_is_capped_at_15(env):
    reg, *_ = env
    for i in range(30):
        reg.handle(ev("PreToolUse", tool_name="Read", tool_input={"file_path": "f%d.py" % i}))
    events = reg.snapshot()["sessions"][0]["events"]
    assert len(events) == 15 and events[-1][1] == "Read f29.py"


def test_prompt_text_never_stored(env):
    reg, *_ = env
    reg.handle(ev("UserPromptSubmit", prompt="my api key is hunter2"))
    reg.handle(ev("Notification", notification_type="permission_prompt", message="hunter2"))
    reg.handle(ev("Stop", last_assistant_message="hunter2"))
    assert "hunter2" not in repr(reg.snapshot())


# ---- summarize ------------------------------------------------------------------------
def test_summaries_are_short_and_content_free():
    assert summarize("Edit", {"file_path": "C:\\proj\\src\\app.py", "new_string": "x" * 999}) == "app.py"
    assert summarize("Read", {"file_path": "/home/me/notes.md"}) == "notes.md"
    long_cmd = "python -c \"" + "a" * 200 + "\""
    s = summarize("Bash", {"command": long_cmd})
    assert len(s) <= 40 and s.endswith("…")
    assert summarize("Bash", {"command": "git status\n&& rm x"}) == "git status && rm x"
    assert summarize("Grep", {"pattern": "password=.*"}) == ""
    assert summarize("WebFetch", {"url": "https://x", "prompt": "secret"}) == ""
    assert short_tool("mcp__github__create_issue") == "create_issue"


def test_response_failed():
    assert response_failed({"is_error": True})
    assert response_failed({"exit_code": 2})
    assert response_failed({"error": "nope"})
    assert not response_failed({"exitCode": 0, "stderr": "warning"})
    assert not response_failed({"interrupted": True, "error": "x"})
    assert not response_failed("plain string")
    assert not response_failed({"code": 404})
