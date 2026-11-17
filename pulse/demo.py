"""`pulse.py demo`: two fake sessions cycling through every state in about a minute."""
import threading
import time

A_CWD = "C:\\code\\pagdandi"
B_CWD = "C:\\code\\drivedex"


def script():
    """(seconds, session, event, extra). PostToolUse carries tool_input, as the real hook does."""
    A, B = "a", "b"
    rows = [
        (0.0, A, "SessionStart", {"source": "startup"}),
        (0.5, B, "SessionStart", {"source": "startup"}),
        (3.0, A, "UserPromptSubmit", {}),
        (4.0, B, "UserPromptSubmit", {}),
        (5.4, B, "PostToolUse", {"tool_name": "Read", "tool_input": {"file_path": "C:\\code\\drivedex\\sync.py"}, "tool_response": {}}),
        (6.8, B, "PostToolUse", {"tool_name": "Grep", "tool_input": {"pattern": "upload"}, "tool_response": {}}),
        (10.6, A, "PostToolUse", {"tool_name": "Read", "tool_input": {"file_path": "C:\\code\\pagdandi\\app.py"}, "tool_response": {}}),
        (12.4, A, "PostToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\pagdandi\\app.py"}, "tool_response": {}}),
        (13.0, B, "PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}),
        (15.4, A, "PostToolUseFailure", {"tool_name": "Bash", "tool_input": {"command": "npm test"}, "error": "Exit code 1"}),
        (17.6, A, "PostToolUse", {"tool_name": "Read", "tool_input": {"file_path": "C:\\code\\pagdandi\\test_app.py"}, "tool_response": {}}),
        (19.5, A, "PostToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\pagdandi\\app.py"}, "tool_response": {}}),
        (23.0, A, "PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "npm test"}, "tool_response": {"stdout": "ok"}}),
        (23.5, B, "PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}, "tool_response": {}}),  # approved: B unblocked
        (25.0, B, "PostToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\drivedex\\sync.py"}, "tool_response": {}}),
        (26.0, A, "PostToolUse", {"tool_name": "Write", "tool_input": {"file_path": "C:\\code\\pagdandi\\routes.py"}, "tool_response": {}}),
        (29.0, A, "PostToolUse", {"tool_name": "Grep", "tool_input": {"pattern": "route"}, "tool_response": {}}),
        (30.0, B, "SubagentStop", {"agent_type": "Explore"}),
        (30.3, B, "PostToolUse", {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore"}, "tool_response": {}}),
        (31.4, A, "PostToolUse", {"tool_name": "Read", "tool_input": {"file_path": "C:\\code\\pagdandi\\models.py"}, "tool_response": {}}),
        (34.0, B, "PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}, "tool_response": {}}),
        (35.0, A, "PostToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\pagdandi\\models.py"}, "tool_response": {}}),
        (39.0, A, "PreCompact", {"trigger": "auto"}),
        (39.2, B, "Stop", {}),
        (41.5, A, "SessionStart", {"source": "compact"}),
        (44.0, A, "PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "npm test"}, "tool_response": {}}),
        (45.5, A, "PostToolUse", {"tool_name": "Edit", "tool_input": {"file_path": "C:\\code\\pagdandi\\README.md"}, "tool_response": {}}),
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
                payload = {"hook_event_name": event, "session_id": ids[who], "cwd": cwds[who]}
                payload.update(extra)
                self.app.ingest(payload)
            wait = start + LOOP_SECONDS / self.speed - time.time()
            if wait > 0 and self._stop.wait(wait):
                return
