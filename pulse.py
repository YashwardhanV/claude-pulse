"""Claude Pulse - a live heartbeat monitor for Claude Code.

    python pulse.py serve            server + window (what auto-start runs)
    python pulse.py demo [--ntfy]    two fake sessions cycling through every state
    python pulse.py install          merge hooks into ~/.claude/settings.json
    python pulse.py uninstall        remove only our hooks
    python pulse.py ntfy-test        send a test phone notification
    python pulse.py status | stop
    python pulse.py ensure-running   (SessionStart hook; reads the hook JSON on stdin)
"""
import argparse
import logging
import logging.handlers
import os
import sys
import time


def _setup_logging(console=True):
    from pulse import config as config_mod
    config_mod.HOME_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("pulse")
    log.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(config_mod.HOME_DIR / "pulse.log",
                                              maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    if console and sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("pulse: %(message)s"))
        log.addHandler(sh)
    return log


def _build(cfg, notify_enabled=None, demo=False):
    from pulse.notifier import Notifier
    from pulse.server import Hub, PulseApp
    from pulse.state import Registry

    hub = Hub()
    notifier = Notifier(cfg, enabled=notify_enabled)
    app = PulseApp(cfg, None, hub, accept_events=not demo)
    app.registry = Registry(cfg, emit=app.emit, notify=notifier.notify)
    return app


def _run_window_or_wait(app, port, cfg, headless, title=None, remember=True, seconds=None, idle_hide=False):
    log = logging.getLogger("pulse")
    url = "http://127.0.0.1:%d/" % port
    if not headless:
        try:
            from pulse.window import TITLE, WindowController
            ctl = WindowController(cfg, url, title=title or TITLE, remember_position=remember,
                                  start_hidden=idle_hide)
            ctl.create()
            on_start = None
            if idle_hide:    # only show the widget while a Claude Code session is live
                ctl.active_fn = app.registry.active_count
                app.on_event = lambda: ctl.sync(app.registry.active_count(), from_event=True)
                app.on_state = lambda: ctl.sync(app.registry.active_count())
                on_start = lambda: ctl.sync(app.registry.active_count(), from_event=True)
            else:
                app.on_event = ctl.show_if_hidden
            if seconds:
                import threading
                import webview
                threading.Timer(seconds, lambda: [w.destroy() for w in list(webview.windows)]).start()
            ctl.run(on_start)  # blocks until the window is closed
            return
        except ImportError:
            log.warning("pywebview is not installed - running headless. Open %s in a browser.", url)
        except Exception:
            log.exception("window failed - running headless. Open %s in a browser.", url)
    try:
        end = time.time() + seconds if seconds else None
        while end is None or time.time() < end:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


def cmd_serve(args):
    from pulse import config as config_mod
    from pulse import launcher
    from pulse.transcript import TranscriptWatcher

    log = _setup_logging(console=sys.stderr is not None)
    cfg = config_mod.load()
    port = int(args.port or cfg.get("port", 7777))
    try:
        if launcher.health(port):
            log.info("Claude Pulse is already running on port %d.", port)
            return 0
        app = _build(cfg)
        try:
            app.start(port=port)
        except OSError as e:
            log.error("cannot listen on 127.0.0.1:%d (%s). Is another program using it? "
                      "Change \"port\" in %s and re-run install.", port, e, config_mod.CONFIG_PATH)
            return 1
    finally:
        launcher.release_start_lock()
    launcher.pid_path().write_text(str(os.getpid()))
    log.info("listening on http://127.0.0.1:%d (pid %d)", port, os.getpid())
    replayed = app.ingest_spool()
    if replayed:
        log.info("replayed %d spooled event(s)", replayed)
    TranscriptWatcher(app.registry).start()
    try:
        # Blocks until the window is closed (× only hides it). Closing it for real, e.g.
        # Alt+F4, stops the server; the next SessionStart hook starts a fresh one.
        headless = args.headless or os.environ.get("PULSE_HEADLESS") == "1"
        _run_window_or_wait(app, port, cfg, headless, idle_hide=True)
        log.info("window closed; exiting")
    finally:
        app.stop()
        try:
            if launcher.pid_path().read_text().strip() == str(os.getpid()):
                launcher.pid_path().unlink()
        except OSError:
            pass
    return 0


def cmd_demo(args):
    from pulse import config as config_mod
    from pulse.demo import DemoDriver

    _setup_logging()
    cfg = dict(config_mod.load())
    if args.ntfy:   # shorter thresholds so the demo actually triggers both notifications
        cfg["notify_blocked_after_seconds"] = 5
        cfg["notify_done_if_session_ran_minutes"] = 0.5
    app = _build(cfg, notify_enabled=bool(args.ntfy), demo=True)
    port = app.start(port=0)     # ephemeral port: never collides with a real server
    print("Claude Pulse demo on http://127.0.0.1:%d/  (close the window or Ctrl+C to stop)" % port)
    if not args.ntfy:
        print("ntfy notifications are skipped in the demo (pass --ntfy to send them).")
    DemoDriver(app, speed=args.speed).start()
    try:
        _run_window_or_wait(app, port, cfg, args.headless, title="Claude Pulse (demo)",
                            remember=False, seconds=args.seconds)
    finally:
        app.stop()
    return 0


def cmd_ensure_running(args):
    # Must return fast and must never fail the hook.
    try:
        from pulse import launcher
        body = launcher.read_stdin()
        launcher.ensure_running(body)
    except Exception:
        pass
    sys.stdout.flush()
    os._exit(0)


def cmd_install(args):
    from pulse import config as config_mod
    from pulse import installer
    cfg = config_mod.load()
    ok = installer.install(int(cfg.get("port", 7777)), pip=not args.no_pip)
    print("Config:   %s" % config_mod.CONFIG_PATH)
    return 0 if ok else 1


def cmd_uninstall(args):
    from pulse import installer
    return 0 if installer.uninstall() else 1


def cmd_ntfy_test(args):
    from pulse import config as config_mod
    from pulse import notifier
    cfg = config_mod.load()
    n = cfg["ntfy"]
    url = "%s/%s" % (n["server"].rstrip("/"), n["topic"])
    print("Topic:  %s" % n["topic"])
    print("Server: %s" % n["server"])
    print("")
    print("To get notifications on your phone:")
    print("  1. Install the ntfy app (Android: Play Store / F-Droid, iPhone: App Store).")
    print("  2. Tap +, 'Subscribe to topic', enter:  %s" % n["topic"])
    print("     (server %s - or open %s in a browser)" % (n["server"], url))
    print("  3. Set \"ntfy\": {\"enabled\": true} in %s" % config_mod.CONFIG_PATH)
    print("Keep the topic secret: anyone who knows it can read these notifications.")
    print("")
    ok, detail = notifier.send(n, "Claude Pulse test", "If you can read this, phone notifications work.",
                               priority=3, tags=["heartbeat"])
    print("Test notification: %s (%s)" % ("sent" if ok else "FAILED", detail))
    if ok and not n.get("enabled"):
        print("Note: ntfy is currently DISABLED in config - real notifications won't be sent until you enable it.")
    return 0 if ok else 1


def cmd_status(args):
    from pulse import config as config_mod
    from pulse import installer, launcher
    cfg = config_mod.load()
    port = int(cfg.get("port", 7777))
    h = launcher.health(port)
    print("Server:  %s" % ("running on port %d (pid %s, up %ss)" % (port, h["pid"], h["uptime"]) if h
                          else "not running (port %d)" % port))
    try:
        settings = installer.load_settings()
        _, n = installer.strip(settings)
        print("Hooks:   %d Claude Pulse hook(s) in %s" % (n, installer.SETTINGS_PATH))
    except ValueError:
        print("Hooks:   settings.json is not valid JSON")
    print("Config:  %s" % config_mod.CONFIG_PATH)
    print("Log:     %s" % (config_mod.HOME_DIR / "pulse.log"))
    print("ntfy:    %s" % ("enabled" if cfg["ntfy"].get("enabled") else "disabled"))
    return 0


def cmd_stop(args):
    from pulse import config as config_mod
    from pulse import launcher
    cfg = config_mod.load()
    h = launcher.health(int(cfg.get("port", 7777)))
    if not h:
        print("Claude Pulse is not running.")
        return 0
    pid = int(h["pid"])
    if sys.platform == "win32":
        import subprocess
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)
    print("Stopped Claude Pulse (pid %d)." % pid)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pulse.py", description="Claude Pulse - heartbeat monitor for Claude Code")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the server and window")
    s.add_argument("--headless", action="store_true", help="no window; view at http://127.0.0.1:<port>/")
    s.add_argument("--port", type=int)
    d = sub.add_parser("demo", help="replay two fake sessions")
    d.add_argument("--ntfy", action="store_true", help="also send phone notifications")
    d.add_argument("--headless", action="store_true")
    d.add_argument("--speed", type=float, default=1.0)
    d.add_argument("--seconds", type=float, help="quit after this many seconds")
    sub.add_parser("ensure-running", help="SessionStart hook: start the server if needed")
    i = sub.add_parser("install", help="merge hooks into ~/.claude/settings.json")
    i.add_argument("--no-pip", action="store_true")
    sub.add_parser("uninstall", help="remove our hooks")
    sub.add_parser("ntfy-test", help="send a test notification")
    sub.add_parser("status")
    sub.add_parser("stop")
    args = p.parse_args(argv)
    handler = {"serve": cmd_serve, "demo": cmd_demo, "ensure-running": cmd_ensure_running,
               "install": cmd_install, "uninstall": cmd_uninstall, "ntfy-test": cmd_ntfy_test,
               "status": cmd_status, "stop": cmd_stop}[args.cmd]
    return handler(args) or 0


if __name__ == "__main__":
    sys.exit(main())
