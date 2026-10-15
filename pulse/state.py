"""Per-session state machine and session registry.

Pure logic: time comes from an injectable clock, side effects go out through callbacks
(`emit(kind, data)` for the UI and `notify(kind, session)` for phone notifications), so the
whole thing is unit-testable without threads or sockets.
"""
import os
import threading
import time
from collections import deque

from .summarize import response_failed, short_tool, summarize

IDLE, WORKING, THINKING, BLOCKED, ERROR, DONE, ENDED = (
    "idle", "working", "thinking", "blocked", "error", "done", "ended")

ERROR_SECONDS = 6
STALE_SECONDS = 15 * 60
STALE_BLOCKED_SECONDS = 60 * 60   # a session waiting on you is kept around longer
REMOVE_AFTER_SECONDS = 10
EVENT_LOG = 15

# Notification types that mean "Claude is waiting on the human".
BLOCKING_NOTIFICATIONS = {
    "permission_prompt": "permission needed",
    "elicitation_dialog": "input requested",
    "elicitation_url_dialog": "input requested",
    "agent_needs_input": "waiting for input",
}
# Tools that are themselves a question to the human.
BLOCKING_TOOLS = {"AskUserQuestion": "question for you", "ExitPlanMode": "plan approval"}


def project_name(cwd):
    if not cwd:
        return "claude"
    return os.path.basename(str(cwd).rstrip("/\\")) or str(cwd)


class Session:
    def __init__(self, sid, cwd, now):
        self.id = sid
        self.cwd = cwd
        self.project = project_name(cwd)
        self.transcript_path = None
        self.created = now
        self.base = IDLE
        self.error_until = None
        self.last_activity = now
        self.last_work = None          # last prompt/tool event — the silence timer
        self.task_started = None
        self.blocked_since = None
        self.blocked_reason = ""
        self.blocked_notified = False
        self.ended_at = None
        self.last_action = "session started"
        self.events = deque(maxlen=EVENT_LOG)
        self.context_tokens = None
        self.context_window = None
        self.context_pct = None
        self.window_hint = None        # 1M if the model id says so
        self.tool_count = 0

    def display_state(self, now):
        if self.base in (ENDED, BLOCKED):
            return self.base
        if self.error_until is not None and now < self.error_until:
            return ERROR
        return self.base

    def log(self, now, text):
        self.events.append((now, text))


class Registry:
    def __init__(self, cfg, clock=time.time, emit=None, notify=None):
        self.cfg = cfg
        self.clock = clock
        self._emit = emit or (lambda kind, data: None)
        self._notify = notify or (lambda kind, session: None)
        self.sessions = {}
        self.lock = threading.RLock()
        self._outbox = []

    # ---- config helpers -------------------------------------------------------------
    @property
    def silence(self):
        return float(self.cfg.get("thinking_silence_seconds", 5))

    # ---- outbox: side effects are flushed outside the lock ----------------------------
    def _queue(self, kind, data):
        self._outbox.append((kind, data))

    def _flush(self):
        out, self._outbox = self._outbox, []
        for kind, data in out:
            if kind == "notify":
                self._notify(*data)
            else:
                self._emit(kind, data)

    def _pulse(self, s, kind, label=""):
        self._queue("pulse", {"sid": s.id, "kind": kind, "label": label})

    # ---- event handling --------------------------------------------------------------
    def handle(self, payload):
        """Apply one raw hook payload. Returns the session id it touched (or None)."""
        if not isinstance(payload, dict):
            return None
        with self.lock:
            sid = self._apply(payload)
            if sid:
                self._queue("state", None)
        self._flush()
        return sid

    def _apply(self, p):
        ev = p.get("hook_event_name") or ""
        sid = str(p.get("session_id") or "")
        if not sid:
            return None
        now = self.clock()
        s = self.sessions.get(sid)
        if s is None or s.base == ENDED:
            if ev == "SessionEnd":
                return None
            s = Session(sid, p.get("cwd"), now)
            self.sessions[sid] = s
        if p.get("cwd") and p.get("cwd") != s.cwd:
            s.cwd, s.project = p["cwd"], project_name(p["cwd"])
        if p.get("transcript_path"):
            s.transcript_path = p["transcript_path"]
        s.last_activity = now

        handler = getattr(self, "_on_" + ev, None)
        if handler:
            handler(s, p, now)
        else:
            s.log(now, ev or "event")
        return sid

    def _set_base(self, s, state, now):
        if s.base == BLOCKED and state != BLOCKED:
            s.blocked_since = None
            s.blocked_reason = ""
        s.base = state

    def _block(self, s, reason, now):
        if s.base != BLOCKED:
            s.blocked_since = now
            s.blocked_notified = False
        s.base = BLOCKED
        s.blocked_reason = reason
        s.last_action = reason
        s.log(now, "blocked · " + reason)

    def _work(self, s, now):
        self._set_base(s, WORKING, now)
        s.last_work = now
        if s.task_started is None:
            s.task_started = now

    def _on_SessionStart(self, s, p, now):
        source = p.get("source") or "startup"
        if "[1m]" in str(p.get("model") or "").lower():
            s.window_hint = 1_000_000
        if source == "clear":
            self._set_base(s, IDLE, now)
            s.task_started = None
        s.log(now, "session " + source)

    def _on_UserPromptSubmit(self, s, p, now):
        self._set_base(s, WORKING, now)
        s.task_started = now
        s.last_work = now
        s.last_action = "prompt sent"
        s.log(now, "prompt sent")

    def _on_PreToolUse(self, s, p, now):
        tool = p.get("tool_name") or "tool"
        label = short_tool(tool)
        summary = summarize(tool, p.get("tool_input"))
        action = (label + " " + summary).strip()
        s.tool_count += 1
        if tool in BLOCKING_TOOLS:
            s.last_work = now
            self._block(s, BLOCKING_TOOLS[tool], now)
        else:
            self._work(s, now)
            s.last_action = action
            s.log(now, action)
        self._pulse(s, "tool", label)

    def _on_PostToolUse(self, s, p, now):
        tool = p.get("tool_name") or "tool"
        self._work(s, now)
        if response_failed(p.get("tool_response")):
            self._error(s, tool, now)

    def _on_PostToolUseFailure(self, s, p, now):
        tool = p.get("tool_name") or "tool"
        if p.get("is_interrupt"):
            # Esc in the terminal: Claude stops, and Stop may not fire.
            self._set_base(s, DONE, now)
            s.task_started = None
            s.last_action = "interrupted"
            s.log(now, short_tool(tool) + " interrupted")
            return
        self._work(s, now)
        self._error(s, tool, now)

    def _error(self, s, tool, now):
        s.error_until = now + ERROR_SECONDS
        s.last_action = short_tool(tool) + " failed"
        s.log(now, s.last_action)
        self._pulse(s, "error", short_tool(tool))

    def _on_PermissionRequest(self, s, p, now):
        self._block(s, "permission for " + short_tool(p.get("tool_name")), now)

    def _on_Notification(self, s, p, now):
        nt = p.get("notification_type") or ""
        if nt in BLOCKING_NOTIFICATIONS:
            if s.base == BLOCKED and nt == "permission_prompt":
                return   # PermissionRequest already put us here with a better reason
            self._block(s, BLOCKING_NOTIFICATIONS[nt], now)
        elif nt == "idle_prompt":
            # Sitting at the prompt. If we never saw Stop, it is waiting on you.
            if s.base in (WORKING, THINKING):
                self._block(s, "waiting for input", now)
            else:
                s.log(now, "idle at prompt")
        else:
            s.log(now, "notification " + (nt or "?"))

    def _on_Stop(self, s, p, now):
        ran = now - s.task_started if s.task_started is not None else 0
        self._set_base(s, DONE, now)
        s.task_started = None
        s.last_action = "finished"
        s.log(now, "finished" + (" (%dm%02ds)" % divmod(int(ran), 60) if ran >= 1 else ""))
        minutes = float(self.cfg.get("notify_done_if_session_ran_minutes", 2))
        if ran >= minutes * 60:
            self._queue("notify", ("done", s))

    def _on_SubagentStop(self, s, p, now):
        s.log(now, "subagent done" + (" · " + str(p["agent_type"])[:20] if p.get("agent_type") else ""))
        self._pulse(s, "flutter")

    def _on_PreCompact(self, s, p, now):
        s.context_pct = None
        s.context_tokens = None
        s.log(now, "compacting (%s)" % (p.get("trigger") or "auto"))
        s.last_action = "compacting context"
        self._pulse(s, "breath")

    def _on_SessionEnd(self, s, p, now):
        s.base = ENDED
        s.ended_at = now
        s.last_action = "session ended"
        s.log(now, "session ended" + (" (%s)" % p["reason"] if p.get("reason") else ""))

    # ---- transcript-driven updates ---------------------------------------------------
    def transcript_thinking(self, sid):
        """Transcript shows a fresh thinking block: confirm `thinking` early."""
        with self.lock:
            s = self.sessions.get(sid)
            if not s or s.base != WORKING:
                return
            s.base = THINKING
            self._queue("state", None)
        self._flush()

    def set_context(self, sid, tokens, model=None):
        learned = None
        with self.lock:
            s = self.sessions.get(sid)
            if not s or tokens is None:
                return
            overrides = self.cfg.get("context_window_overrides") or {}
            window = (s.window_hint or (model and overrides.get(model))
                      or int(self.cfg.get("context_window_tokens", 200000)))
            if tokens > window:
                window = max(window, 1_000_000)   # extended-context model
                if model and overrides.get(model) != window:
                    learned = (model, window)
                    self.cfg.setdefault("context_window_overrides", {})[model] = window
            s.context_tokens = int(tokens)
            s.context_window = window
            s.context_pct = round(100.0 * tokens / window, 1)
            self._queue("state", None)
            if learned:
                self._queue("learn_window", learned)
        self._flush()

    # ---- timers ----------------------------------------------------------------------
    def tick(self):
        """Advance time-based transitions. Call ~2x per second."""
        now = self.clock()
        changed = False
        with self.lock:
            blocked_after = float(self.cfg.get("notify_blocked_after_seconds", 30))
            for sid, s in list(self.sessions.items()):
                if s.base == WORKING and s.last_work is not None and now - s.last_work >= self.silence:
                    s.base = THINKING
                    changed = True
                if s.error_until is not None and now >= s.error_until:
                    s.error_until = None
                    changed = True
                if (s.base == BLOCKED and not s.blocked_notified and s.blocked_since is not None
                        and now - s.blocked_since >= blocked_after):
                    s.blocked_notified = True
                    self._queue("notify", ("blocked", s))
                if s.base != ENDED:
                    limit = STALE_BLOCKED_SECONDS if s.base == BLOCKED else STALE_SECONDS
                    if now - s.last_activity >= limit:
                        s.base = ENDED
                        s.ended_at = now
                        s.last_action = "no activity"
                        s.log(now, "ended (no activity for %d min)" % (limit // 60))
                        changed = True
                elif now - s.ended_at >= REMOVE_AFTER_SECONDS:
                    del self.sessions[sid]
                    changed = True
            if changed:
                self._queue("state", None)
        self._flush()
        return changed

    # ---- views -----------------------------------------------------------------------
    def get(self, sid):
        return self.sessions.get(sid)

    def state_of(self, sid):
        s = self.sessions.get(sid)
        return s.display_state(self.clock()) if s else None

    def snapshot(self):
        now = self.clock()
        with self.lock:
            sessions = list(self.sessions.values())
            per_project = {}
            for s in sessions:
                if s.base != ENDED:
                    per_project[s.project] = per_project.get(s.project, 0) + 1
            sessions.sort(key=lambda s: (0 if s.base == BLOCKED else 1,
                                         s.blocked_since or 0, s.created))
            out = []
            for s in sessions:
                state = s.display_state(now)
                title = s.project
                if per_project.get(s.project, 0) > 1:
                    title += " · " + s.id[:4]
                detail = s.blocked_reason if state == BLOCKED else s.last_action
                out.append({
                    "id": s.id,
                    "title": title,
                    "project": s.project,
                    "state": state,
                    "status": state + (" · " + detail if detail else ""),
                    "blocked_for": round(now - s.blocked_since) if s.blocked_since else None,
                    "context_pct": s.context_pct,
                    "context_tokens": s.context_tokens,
                    "context_window": s.context_window,
                    "tools": s.tool_count,
                    "ended_ago": round(now - s.ended_at, 1) if s.ended_at else None,
                    "events": [[round(ts * 1000), text] for ts, text in s.events],
                })
            return {"now": round(now * 1000), "sessions": out}
