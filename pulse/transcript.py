"""Tail each session's transcript JSONL for thinking blocks and context usage."""
import json
import logging
import os
import threading

log = logging.getLogger("pulse")

INITIAL_TAIL = 2 * 1024 * 1024   # bytes scanned when we first attach to a transcript


def usage_tokens(usage):
    """Context in use = input + cache read + cache creation (same as the status line)."""
    if not isinstance(usage, dict):
        return None
    total = 0
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        val = usage.get(key)
        if isinstance(val, (int, float)):
            total += int(val)
    return total or None


def parse_assistant(entry):
    """-> (tokens or None, has_thinking, has_output) for a main-chain assistant entry, else None."""
    if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
        return None
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return None
    kinds = {c.get("type") for c in msg.get("content") or [] if isinstance(c, dict)}
    return (usage_tokens(msg.get("usage")), "thinking" in kinds or "redacted_thinking" in kinds,
            bool(kinds & {"text", "tool_use"}))


class FileTail:
    """Incremental line reader that tolerates a partially written last line."""

    def __init__(self, path, initial_tail=INITIAL_TAIL):
        self.path = path
        self.offset = 0
        self.buf = b""
        self.first = True
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size > initial_tail:
            self.offset = size - initial_tail
            self.skip_partial = True     # we landed mid-line
        else:
            self.skip_partial = False

    def read_lines(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.offset:           # file was truncated/replaced
            self.offset, self.buf, self.skip_partial = 0, b"", False
        if size == self.offset:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read(size - self.offset)
        self.offset += len(data)
        self.buf += data
        *lines, self.buf = self.buf.split(b"\n")
        if self.skip_partial and lines:
            lines = lines[1:]
            self.skip_partial = False
        out = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


class TranscriptWatcher:
    def __init__(self, registry, interval=1.0):
        self.registry = registry
        self.interval = interval
        self.tails = {}
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="transcripts", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.poll()
            except Exception:
                log.exception("transcript poll failed")

    def poll(self):
        with self.registry.lock:
            targets = {sid: s.transcript_path for sid, s in self.registry.sessions.items()
                       if s.transcript_path}
        for sid in list(self.tails):
            if sid not in targets:
                del self.tails[sid]
        for sid, path in targets.items():
            tail = self.tails.get(sid)
            if tail is None or tail.path != path:
                if not os.path.isfile(path):
                    continue
                tail = self.tails[sid] = FileTail(path)
            self._process(sid, tail)

    def _process(self, sid, tail):
        initial = tail.first
        tail.first = False
        tokens, last = None, None
        for entry in tail.read_lines():
            info = parse_assistant(entry)
            if info is None:
                continue
            if info[0]:
                tokens = info[0]
            last = info
        if tokens:
            self.registry.set_context(sid, tokens)
        # Latest assistant block is a thinking block with nothing after it: still reasoning.
        if not initial and last and last[1] and not last[2]:
            self.registry.transcript_thinking(sid)
