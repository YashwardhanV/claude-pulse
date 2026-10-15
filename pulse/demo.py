"""`pulse.py demo`: two fake sessions cycling through every state in about a minute."""
import threading
import time

A_CWD = "C:\\code\\pagdandi"
B_CWD = "C:\\code\\drivedex"


def script():
    """(seconds, session, event, extra) — 'ctx' rows set context usage instead."""
    A, B = "a", "b"
    rows = [
        (0.0, A, "SessionStart", {"source": "startup"}),
        (0.5, B, "SessionStart", {"source": "startup"}),
        (3.0, A, "UserPromptSubmit", {}),                      # A: silent → thinking at ~8s
        (3.5, A, "ctx", 12),
        (4.0, B, "UserPromptSubmit", {}),
        (4.6, B, "PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "C:\\code\\drivedex\\sync.py"}}),
        (5.4, B, "PostToolUse", {"tool_name": "Read", "tool_response": {}}),
        (6.2, B, "PreToolUse", {"tool_name": "Grep", "tool_input": {"pattern": "upload"}}),
        (6.8, B, "PostToolUse", {"tool_name": "Grep", "tool_response": {}}),
        (10.0, A, "PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "C:\\code\\pagdandi\\app.py"}}),
        (10.6, A, "PostToolUse", {"tool_name": "Read", "tool_response": {}}),
        (11.0, A, "ctx", 31),
        (11.8, A, "PreToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\pagdandi\\app.py"}}),
        (12.4, A, "PostToolUse", {"tool_name": "Edit", "tool_response": {}}),
        (13.0, B, "PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}),
        (13.6, A, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "npm test"}}),
        (15.4, A, "PostToolUseFailure", {"tool_name": "Bash", "error": "Exit code 1"}),
        (17.0, A, "PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "C:\\code\\pagdandi\\test_app.py"}}),
        (17.6, A, "PostToolUse", {"tool_name": "Read", "tool_response": {}}),
        (19.0, A, "PreToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\pagdandi\\app.py"}}),
        (19.5, A, "PostToolUse", {"tool_name": "Edit", "tool_response": {}}),
        (20.0, A, "ctx", 48),
        (21.5, A, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "npm test"}}),
        (23.0, A, "PostToolUse", {"tool_name": "Bash", "tool_response": {"stdout": "ok"}}),
        (23.5, B, "PostToolUse", {"tool_name": "Bash", "tool_response": {}}),    # B unblocked
        (24.0, A, "ctx", 62),
        (24.5, B, "PreToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\drivedex\\sync.py"}}),
        (25.0, B, "PostToolUse", {"tool_name": "Edit", "tool_response": {}}),
        (25.5, A, "PreToolUse", {"tool_name": "Write", "tool_input": {"file_path": "C:\\code\\pagdandi\\routes.py"}}),
        (26.0, A, "PostToolUse", {"tool_name": "Write", "tool_response": {}}),
        (27.0, B, "PreToolUse", {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore"}}),
        (28.0, A, "ctx", 79),
        (28.5, A, "PreToolUse", {"tool_name": "Grep", "tool_input": {"pattern": "route"}}),
        (29.0, A, "PostToolUse", {"tool_name": "Grep", "tool_response": {}}),
        (30.0, B, "SubagentStop", {"agent_type": "Explore"}),
        (30.3, B, "PostToolUse", {"tool_name": "Agent", "tool_response": {}}),
        (31.0, A, "PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "C:\\code\\pagdandi\\models.py"}}),
        (31.4, A, "PostToolUse", {"tool_name": "Read", "tool_response": {}}),
        (32.0, A, "ctx", 93),
        (32.5, B, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}),
        (34.0, B, "PostToolUse", {"tool_name": "Bash", "tool_response": {}}),
        (34.5, A, "PreToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\pagdandi\\models.py"}}),
        (35.0, A, "PostToolUse", {"tool_name": "Edit", "tool_response": {}}),
        (36.0, A, "ctx", 96),
        (39.0, A, "PreCompact", {"trigger": "auto"}),
        (39.2, B, "Stop", {}),
        (41.5, A, "SessionStart", {"source": "compact"}),
        (42.0, A, "ctx", 17),
        (42.5, A, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "npm test"}}),
        (44.0, A, "PostToolUse", {"tool_name": "Bash", "tool_response": {}}),
        (45.0, A, "PreToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\pagdandi\\README.md"}}),
        (45.5, A, "PostToolUse", {"tool_name": "Edit", "tool_response": {}}),
        (46.5, A, "Stop", {}),
        (51.0, B, "SessionEnd", {"reason": "prompt_input_exit"}),
        (54.0, A, "SessionEnd", {"reason": "prompt_input_exit"}),
    ]
    return rows


LOOP_SECONDS = 66   # ends at 54s, cards are removed 10s later


class DemoDriver:
    def __init__(self, app, speed=1.0):
        self.app = app
        self.speed = speed
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="demo", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        loop = 0
        while not self._stop.is_set():
            loop += 1
            ids = {"a": "pagdandi-demo%04d" % loop, "b": "drivedex-demo%04d" % loop}
            cwds = {"a": A_CWD, "b": B_CWD}
            start = time.time()
            for at, who, event, extra in script():
                wait = start + at / self.speed - time.time()
                if wait > 0 and self._stop.wait(wait):
                    return
                if event == "ctx":
                    self.app.registry.set_context(ids[who], int(extra * 2000))
                    continue
                payload = {"hook_event_name": event, "session_id": ids[who], "cwd": cwds[who]}
                payload.update(extra)
                self.app.ingest(payload)
            wait = start + LOOP_SECONDS / self.speed - time.time()
            if wait > 0 and self._stop.wait(wait):
                return
