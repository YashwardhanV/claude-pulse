"""HTTP server: hook intake, SSE stream for the window, static page."""
import json
import logging
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as config_mod

log = logging.getLogger("pulse")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 32 * 1024 * 1024   # PostToolUse can carry a whole file in tool_response
APP_ID = "claude-pulse"


class Hub:
    """Fan-out of server-sent events to every connected page."""

    def __init__(self):
        self._clients = []
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def broadcast(self, event, data):
        msg = ("event: %s\ndata: %s\n\n" % (event, json.dumps(data, separators=(",", ":")))).encode()
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


class PulseApp:
    """Glue: registry + hub + timers + optional hooks for the window."""

    def __init__(self, cfg, registry, hub, on_event=None, accept_events=True):
        self.cfg = cfg
        self.registry = registry
        self.hub = hub
        self.on_event = on_event          # e.g. show a hidden window
        self.on_state = None              # called on every state change (hides the window when idle)
        self.accept_events = accept_events
        self.started = time.time()
        self._stop = threading.Event()
        self.httpd = None

    # -- registry callbacks ------------------------------------------------------------
    def emit(self, kind, data):
        if kind == "state":
            self.hub.broadcast("state", self.registry.snapshot())
            if self.on_state:
                try:
                    self.on_state()
                except Exception:
                    log.exception("on_state failed")
        elif kind == "learn_window":
            model, window = data
            log.info("learned: %s has a %d-token context window", model, window)
            try:
                config_mod.update(lambda c: c.setdefault("context_window_overrides", {}).update({model: window}))
            except Exception:
                log.exception("saving context_window_overrides failed")
        else:
            self.hub.broadcast(kind, data)

    def ingest(self, payload):
        sid = self.registry.handle(payload)
        if sid:
            log.info("event %s %s", payload.get("hook_event_name"), sid[:8])
            if self.on_event:
                try:
                    self.on_event()
                except Exception:
                    log.exception("on_event failed")
        return sid

    # -- lifecycle ---------------------------------------------------------------------
    def start(self, host="127.0.0.1", port=None):
        port = int(port if port is not None else self.cfg.get("port", 7777))
        handler = type("Handler", (_Handler,), {"app": self})
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True).start()
        threading.Thread(target=self._ticker, name="tick", daemon=True).start()
        return self.httpd.server_address[1]

    def stop(self):
        self._stop.set()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    def _ticker(self):
        last_beat = 0
        while not self._stop.wait(0.5):
            try:
                changed = self.registry.tick()
                if not changed and time.time() - last_beat > 5:
                    self.emit("state", None)    # keeps "blocked for 40s" counters fresh
                    last_beat = time.time()
            except Exception:
                log.exception("tick failed")

    def ingest_spool(self, max_age=120):
        """Replay hook payloads saved by `ensure-running` while the server was down."""
        spool = config_mod.HOME_DIR / "spool"
        if not spool.is_dir():
            return 0
        n = 0
        for f in sorted(spool.glob("*.json")):
            try:
                if time.time() - f.stat().st_mtime < max_age:
                    self.ingest(json.loads(f.read_text(encoding="utf-8")))
                    n += 1
            except Exception:
                pass
            try:
                f.unlink()
            except OSError:
                pass
        return n


class _Handler(BaseHTTPRequestHandler):
    app = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # keep request lines out of the log
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            body = json.dumps({"app": APP_ID, "pid": os.getpid(),
                               "uptime": round(time.time() - self.app.started)}).encode()
            return self._send(200, body)
        if path == "/state":
            return self._send(200, json.dumps(self.app.registry.snapshot()).encode())
        if path == "/stream":
            return self._stream()
        if path in ("/", "/index.html"):
            try:
                body = (WEB_DIR / "index.html").read_bytes()
            except OSError:
                return self._send(500, b"index.html missing", "text/plain")
            return self._send(200, body, "text/html; charset=utf-8")
        self._send(404, b"{}")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        if path != "/event":
            self.rfile.read(min(length, MAX_BODY))
            return self._send(404, b"{}")
        if length <= 0 or length > MAX_BODY:
            self.close_connection = True
            return self._send(413 if length > MAX_BODY else 400)
        raw = self.rfile.read(length)
        # Answer first so the hook's curl returns immediately; process afterwards.
        self._send(204)
        if not self.app.accept_events:
            return
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            log.warning("ignored a non-JSON event body (%d bytes)", len(raw))
            return
        del raw
        try:
            self.app.ingest(payload)
        except Exception:
            log.exception("bad event")

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True
        q = self.app.hub.subscribe()
        try:
            snap = json.dumps(self.app.registry.snapshot(), separators=(",", ":"))
            self.wfile.write(("retry: 1000\nevent: state\ndata: %s\n\n" % snap).encode())
            self.wfile.flush()
            while not self.app._stop.is_set():
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    msg = b": keepalive\n\n"
                self.wfile.write(msg)
                self.wfile.flush()
        except (OSError, ValueError):
            pass
        finally:
            self.app.hub.unsubscribe(q)
