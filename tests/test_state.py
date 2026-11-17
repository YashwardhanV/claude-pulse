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
    "notify_blocked_after_seconds": 30,
    "notify_done_if_session_ran_minutes": 2,
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
    reg.handle(ev("PostToolUse", sid="late", tool_name="Read", tool_input={"file_path": "/a/b.py"}))
    assert reg.state_of("late") == "working"


def test_payload_without_session_id_is_ignored(env):
    reg, *_ = env
    assert reg.handle({"hook_event_name": "Stop"}) is None
    assert reg.handle("not a dict") is None
    assert reg.sessions == {}
















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
    assert snap["status"] == "needs you · permission needed"
    clock.advance(10)
    reg.handle(ev("PostToolUse", tool_name="Bash", tool_response={}))
    assert reg.state_of("s1") == "working"
    clock.advance(60)
    reg.tick()
    assert notified == []                 # answered quickly: no phone ping


def test_blocked_beats_error_and_time(env):
    reg, clock, _, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("PostToolUseFailure", tool_name="Bash", error="Exit code 1"))
    reg.handle(ev("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf x"}))
    assert reg.state_of("s1") == "blocked"
    clock.advance(20)
    reg.tick()
    assert reg.state_of("s1") == "blocked"
    assert reg.snapshot()["sessions"][0]["status"] == "needs you · permission for Bash"


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
        reg.handle(ev("PostToolUse", tool_name="Read", tool_input={"file_path": "f%d.py" % i}))
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


def test_active_count_drops_to_zero_when_sessions_end(env):
    reg = env[0]
    assert reg.active_count() == 0
    reg.handle(ev("SessionStart", sid="a"))
    reg.handle(ev("SessionStart", sid="b"))
    assert reg.active_count() == 2
    reg.handle(ev("SessionEnd", sid="a"))
    reg.handle(ev("SessionEnd", sid="b"))
    assert reg.active_count() == 0


def test_active_ids_excludes_ended_sessions(env):
    reg, clock, _, _ = env
    reg.handle(ev("SessionStart", sid="a"))
    reg.handle(ev("SessionStart", sid="b"))
    reg.handle(ev("SessionEnd", sid="b"))
    assert reg.active_ids() == {"a"}


# ---- three visible states -------------------------------------------------------------
def test_prompt_stays_working_until_stop(env):
    reg, clock, _, _ = env
    reg.handle(ev("UserPromptSubmit", prompt="secret prompt"))
    clock.advance(60)                      # no "thinking" state any more: silence is still work
    reg.tick()
    assert reg.state_of("s1") == "working"
    reg.handle(ev("Stop"))
    assert reg.state_of("s1") == "done"


def test_tool_results_drive_spikes_and_status(env):
    reg, clock, emitted, _ = env
    reg.handle(ev("UserPromptSubmit"))
    for _ in range(3):
        clock.advance(4)
        reg.handle(ev("PostToolUse", tool_name="Edit",
                      tool_input={"file_path": "C:\\x\\app.py", "old_string": "SECRET"}, tool_response={}))
    s = reg.get("s1")
    assert reg.state_of("s1") == "working" and s.last_action == "Edit app.py" and s.tool_count == 3
    assert "SECRET" not in repr(reg.snapshot())
    assert [p["label"] for p in pulses(emitted)] == ["Edit", "Edit", "Edit"]


def test_ordinary_pretooluse_is_not_a_spike(env):
    """Old installs still send PreToolUse for every tool: no double spikes."""
    reg, _, emitted, _ = env
    reg.handle(ev("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"}))
    assert reg.state_of("s1") == "working" and pulses(emitted) == []


def test_failure_is_a_red_spike_not_a_state(env):
    reg, _, emitted, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("PostToolUseFailure", tool_name="Bash", tool_input={"command": "npm test"},
                  error="Exit code 1\nboom"))
    assert reg.state_of("s1") == "working"
    assert reg.get("s1").last_action == "Bash failed"
    assert pulses(emitted)[-1]["kind"] == "error"


def test_post_tool_use_error_detection(env):
    reg, *_ = env
    reg.handle(ev("PostToolUse", tool_name="Bash", tool_response={"stdout": "", "exitCode": 1}))
    assert reg.get("s1").last_action == "Bash failed"


def test_snapshot_shows_only_working_needs_you_done(env):
    reg, *_ = env
    reg.handle(ev("SessionStart", sid="a"))
    reg.handle(ev("UserPromptSubmit", sid="b"))
    reg.handle(ev("PermissionRequest", sid="c", tool_name="Bash"))
    snap = {x["id"]: x for x in reg.snapshot()["sessions"]}
    assert (snap["a"]["state"], snap["a"]["badge"]) == ("done", "ready")
    assert (snap["b"]["state"], snap["b"]["badge"]) == ("working", "working")
    assert (snap["c"]["state"], snap["c"]["badge"]) == ("blocked", "needs you")


def test_precompact_breath(env):
    reg, _, emitted, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("PreCompact", trigger="auto"))
    assert pulses(emitted)[-1]["kind"] == "breath"
    assert reg.get("s1").last_action == "compacting context"


# ---- async tool hooks: a late result must not undo done / needs you -------------------
def test_late_tool_result_after_stop_keeps_done(env):
    reg, clock, emitted, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("Stop"))
    clock.advance(0.2)
    reg.handle(ev("PostToolUse", tool_name="Read", tool_input={"file_path": "a.py"}, tool_response={}))
    assert reg.state_of("s1") == "done"
    assert pulses(emitted)[-1]["label"] == "Read"          # still drawn on the ECG
    assert reg.get("s1").events[-1][1] == "Read a.py"


def test_late_tool_result_after_permission_keeps_blocked(env):
    reg, clock, _, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build"}))
    clock.advance(0.3)
    reg.handle(ev("PostToolUse", tool_name="Read", tool_input={"file_path": "a.py"}, tool_response={}))
    reg.handle(ev("PostToolUseFailure", tool_name="Grep", tool_input={"pattern": "x"}, error="boom"))
    assert reg.state_of("s1") == "blocked"


def test_approved_call_finishing_quickly_unblocks(env):
    reg, clock, _, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build"}))
    clock.advance(1)                       # you approved within a second
    reg.handle(ev("PostToolUse", tool_name="Bash", tool_input={"command": "rm -rf build"}, tool_response={}))
    assert reg.state_of("s1") == "working"


def test_answered_question_unblocks(env):
    reg, clock, _, _ = env
    q = {"questions": [{"question": "Which db?"}]}
    reg.handle(ev("PreToolUse", tool_name="AskUserQuestion", tool_input=q))
    assert reg.state_of("s1") == "blocked"
    clock.advance(1)
    reg.handle(ev("PostToolUse", tool_name="AskUserQuestion", tool_input=q, tool_response={}))
    assert reg.state_of("s1") == "working"


def test_tool_result_well_after_stop_counts_again(env):
    reg, clock, _, _ = env
    reg.handle(ev("UserPromptSubmit"))
    reg.handle(ev("Stop"))
    clock.advance(5)
    reg.handle(ev("PostToolUse", tool_name="Read", tool_input={"file_path": "a.py"}, tool_response={}))
    assert reg.state_of("s1") == "working"


def test_windows_cwd_gives_project_name_on_any_os():
    from pulse.state import project_name
    assert project_name("C:\\code\\pagdandi\\") == "pagdandi"
    assert project_name("/home/y/code/drivedex") == "drivedex"
    assert project_name("") == "claude"
