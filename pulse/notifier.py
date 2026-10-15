"""Phone notifications through ntfy.

Privacy: ntfy topics are readable by anyone who knows the name, so a notification carries only
the project folder name and the event type. Never code, prompts, commands or file names.
"""
import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger("pulse")

DEBOUNCE_SECONDS = 10


def build(kind, project):
    if kind == "blocked":
        return {"title": "Claude needs you — %s" % project,
                "message": "%s is waiting for you." % project,
                "priority": 4, "tags": ["warning"]}
    if kind == "done":
        return {"title": "Claude finished — %s" % project,
                "message": "%s finished its task." % project,
                "priority": 3, "tags": ["white_check_mark"]}
    return {"title": "Claude Pulse — %s" % project, "message": kind, "priority": 3, "tags": []}


def send(ntfy_cfg, title, message, priority=3, tags=(), timeout=8):
    """Publish via ntfy's JSON API (keeps UTF-8 titles intact). Returns (ok, detail)."""
    server = (ntfy_cfg.get("server") or "https://ntfy.sh").rstrip("/")
    body = json.dumps({"topic": ntfy_cfg["topic"], "title": title, "message": message,
                       "priority": int(priority), "tags": list(tags)}).encode("utf-8")
    req = urllib.request.Request(server, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300, "HTTP %d" % resp.status
    except Exception as e:      # network errors must never take the server down
        return False, str(e)


class Notifier:
    def __init__(self, cfg, enabled=None, sender=send):
        self.cfg = cfg
        self.enabled_override = enabled
        self.sender = sender
        self._last = {}

    @property
    def enabled(self):
        if self.enabled_override is not None:
            return self.enabled_override
        return bool(self.cfg.get("ntfy", {}).get("enabled"))

    def notify(self, kind, session):
        if not self.enabled or not self.cfg.get("ntfy", {}).get("topic"):
            return False
        key = (session.id, kind)
        now = time.time()
        if now - self._last.get(key, 0) < DEBOUNCE_SECONDS:
            return False
        self._last[key] = now
        msg = build(kind, session.project)
        threading.Thread(target=self._send, args=(kind, session.id, msg), daemon=True).start()
        return True

    def _send(self, kind, sid, msg):
        ok, detail = self.sender(self.cfg["ntfy"], msg["title"], msg["message"],
                                 msg["priority"], msg["tags"])
        log.info("ntfy %s %s: %s", kind, sid[:8], detail if ok else "FAILED " + detail)
