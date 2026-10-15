import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from pulse import notifier
from pulse.notifier import Notifier, build
from pulse.state import Registry


class FakeSession:
    def __init__(self, sid="abc12345", project="pagdandi"):
        self.id, self.project = sid, project


def wait_for(cond, timeout=2.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_messages_contain_only_project_and_event_type():
    b = build("blocked", "pagdandi")
    assert b["title"] == "Claude needs you — pagdandi" and b["priority"] == 4
    d = build("done", "pagdandi")
    assert d["title"] == "Claude finished — pagdandi" and d["priority"] == 3


def test_disabled_sends_nothing():
    sent = []
    n = Notifier({"ntfy": {"enabled": False, "topic": "t"}}, sender=lambda *a: sent.append(a) or (True, ""))
    assert n.notify("blocked", FakeSession()) is False
    assert sent == []


def test_debounce_per_session_and_kind():
    sent = []
    cfg = {"ntfy": {"enabled": True, "topic": "t", "server": "https://ntfy.sh"}}
    n = Notifier(cfg, sender=lambda *a: sent.append(a) or (True, "ok"))
    assert n.notify("blocked", FakeSession())
    assert not n.notify("blocked", FakeSession())         # same state change burst
    assert n.notify("done", FakeSession())
    assert n.notify("blocked", FakeSession(sid="other"))
    assert wait_for(lambda: len(sent) == 3)


def test_end_to_end_registry_to_ntfy_server_never_leaks_content():
    """Real HTTP: registry -> Notifier -> a local fake ntfy server."""
    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = {"ntfy": {"enabled": True, "topic": "secret-topic-123",
                        "server": "http://127.0.0.1:%d" % srv.server_address[1]},
               "notify_blocked_after_seconds": 30, "notify_done_if_session_ran_minutes": 2,
               "thinking_silence_seconds": 5}
        t = [0.0]
        reg = Registry(cfg, clock=lambda: t[0], notify=Notifier(cfg).notify)
        base = {"session_id": "s1", "cwd": "C:\\code\\pagdandi"}
        reg.handle({**base, "hook_event_name": "UserPromptSubmit", "prompt": "TOPSECRET prompt"})
        reg.handle({**base, "hook_event_name": "PermissionRequest", "tool_name": "Bash",
                    "tool_input": {"command": "TOPSECRET command"}})
        t[0] = 31
        reg.tick()
        reg.handle({**base, "hook_event_name": "PostToolUse", "tool_name": "Bash",
                    "tool_response": {"stdout": "TOPSECRET output"}})
        t[0] = 200
        reg.handle({**base, "hook_event_name": "Stop", "last_assistant_message": "TOPSECRET"})
        assert wait_for(lambda: len(received) == 2)
        titles = sorted(r["title"] for r in received)
        assert titles == ["Claude finished — pagdandi", "Claude needs you — pagdandi"]
        assert all(r["topic"] == "secret-topic-123" for r in received)
        assert "TOPSECRET" not in json.dumps(received)
        assert "Bash" not in json.dumps(received)
    finally:
        srv.shutdown()


def test_send_reports_network_failure_without_raising():
    ok, detail = notifier.send({"server": "http://127.0.0.1:9", "topic": "x"}, "t", "m", timeout=1)
    assert ok is False and detail
