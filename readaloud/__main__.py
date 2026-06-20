"""readaloud CLI entry point.

Usage:
    readaloud --stdin [--config PATH]
    readaloud --window [--config PATH]      (text also arrives on stdin)
    readaloud --print-config-json [--config PATH]
    readaloud --print-script [--config PATH]   (read stdin, print speech script JSON)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .clean import clean
from .config import ConfigError, load_config
from .parse import parse
from .script import build_script


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "readaloud"
    return Path.home() / ".local" / "state" / "readaloud"


def _pidfile() -> Path:
    return _state_dir() / "readaloud.pid"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _acquire_single_instance() -> bool:
    """Acquire the single-instance lock. Returns False if another reader runs.

    Stale pidfiles (dead pid) are reclaimed. The current pid is written.
    """
    pf = _pidfile()
    pf.parent.mkdir(parents=True, exist_ok=True)
    if pf.exists():
        try:
            existing = int(pf.read_text().strip() or "0")
        except (ValueError, OSError):
            existing = 0
        if existing and existing != os.getpid() and _pid_alive(existing):
            return False
    pf.write_text(str(os.getpid()))
    return True


def _release_single_instance() -> None:
    pf = _pidfile()
    try:
        if pf.exists() and pf.read_text().strip() == str(os.getpid()):
            pf.unlink()
    except OSError:
        pass


def _make_engine(cfg: dict[str, Any]):
    engine_name = cfg.get("engine", "say")
    if engine_name == "kokoro":
        from .engines.kokoro_engine import KokoroEngine

        return KokoroEngine(cfg)
    from .engines.say_engine import SayEngine

    return SayEngine(cfg)


def _read_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def _truncate(text: str, cfg: dict[str, Any]) -> str:
    limit = int(cfg.get("limits", {}).get("max_selection_chars", 60000))
    if len(text) > limit:
        logging.getLogger("readaloud").warning(
            "input %d chars exceeds max_selection_chars %d; truncating",
            len(text),
            limit,
        )
        return text[:limit]
    return text


def _window_truncate(text: str, cfg: dict[str, Any]) -> str:
    limit = int(cfg.get("window_read", {}).get("max_chars", 20000))
    if len(text) > limit:
        return text[:limit]
    return text


def _send_to_daemon(cmd: str, app: str | None) -> int:
    """Relay a command to the warm daemon over its unix socket.

    Lazy-starts the daemon (detached) if it isn't running, then issues the
    command. ``read`` reads text from stdin and BLOCKS until the daemon reports
    playback done (so the caller's process lifetime ~= playback duration, which
    Hammerspoon relies on to know when to dismiss the transport pill).
    ``pause``/``stop`` send and return immediately on ack.
    """
    import socket as _socket
    import subprocess
    import time

    from . import daemon as _daemon

    sock_path = str(_daemon.socket_path())

    def _connect() -> "_socket.socket":
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.connect(sock_path)
        return s

    # Connect; a successful connect means the daemon has bound its socket (it
    # binds before loading the model and serves the request once its accept loop
    # starts, so no separate readiness ping is needed). If connect fails, the
    # daemon isn't running — lazy-start it detached and poll connect() until the
    # socket is accepting, or time out.
    try:
        s = _connect()
    except OSError:
        logpath = _daemon.daemon_log_path()
        logpath.parent.mkdir(parents=True, exist_ok=True)
        logf = open(logpath, "ab")  # noqa: SIM115 (handed to the child)
        subprocess.Popen(
            [sys.executable, "-m", "readaloud.daemon"],
            stdout=logf,
            stderr=logf,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 12.0  # model load ~1s; generous headroom
        s = None
        while time.monotonic() < deadline:
            try:
                s = _connect()
                break
            except OSError:
                time.sleep(0.1)
        if s is None:
            print("readaloud: daemon did not become ready", file=sys.stderr)
            return 3
    # Send via sendall (raw socket write); read the daemon's replies via a
    # makefile read buffer. The daemon's _handle reads our line and writes
    # back newline-delimited JSON.
    try:
        if cmd == "read":
            req = {"cmd": "read", "text": _read_stdin(), "app": app}
            s.sendall((json.dumps(req) + "\n").encode())
            rf = s.makefile("rb")
            while True:  # block until playback done / connection closed
                line = rf.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace").strip())
                except json.JSONDecodeError:
                    continue
                if msg.get("event") == "done":
                    break
        else:  # pause | stop
            s.sendall((json.dumps({"cmd": cmd}) + "\n").encode())
            s.makefile("rb").readline()  # await ack
    finally:
        try:
            s.close()
        except OSError:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="readaloud: %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(prog="readaloud", description=__doc__)
    parser.add_argument("--stdin", action="store_true", help="read text from stdin")
    parser.add_argument(
        "--window",
        action="store_true",
        help="read window text from stdin (applies window_read.max_chars)",
    )
    parser.add_argument("--config", metavar="PATH", help="config file path override")
    parser.add_argument(
        "--print-config-json",
        action="store_true",
        help="print merged config as JSON and exit",
    )
    parser.add_argument(
        "--print-script",
        action="store_true",
        help="run clean+parse+script on stdin and print chunks as JSON",
    )
    parser.add_argument("--version", action="version", version=f"readaloud {__version__}")
    parser.add_argument("--app", metavar="NAME", help="frontmost app name, for per-app mute rules")
    parser.add_argument("--daemon", action="store_true", help="run the warm kokoro daemon")
    parser.add_argument(
        "--send",
        metavar="CMD",
        choices=["read", "pause", "stop"],
        help="send a command to the daemon (lazy-starts it); read takes stdin",
    )
    args = parser.parse_args(argv)

    # Daemon + client paths run BEFORE load_config: the daemon loads config
    # fresh per read, and the client never touches config (it just relays).
    if args.daemon:
        from .daemon import main as daemon_main

        return daemon_main()

    if args.send:
        return _send_to_daemon(args.send, args.app)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"readaloud: config error: {exc}", file=sys.stderr)
        return 2

    if args.print_config_json:
        json.dump(cfg, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
        return 0

    raw = _read_stdin()

    if args.print_script:
        cleaned = clean(raw, cfg, app=args.app)
        chunks = build_script(parse(cleaned, cfg), cfg)
        json.dump([c.to_dict() for c in chunks], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if not (args.stdin or args.window):
        parser.error("one of --stdin, --window, --print-config-json, --print-script is required")

    if args.window:
        raw = _window_truncate(raw, cfg)
    else:
        raw = _truncate(raw, cfg)

    cleaned = clean(raw, cfg, app=args.app)
    chunks = build_script(parse(cleaned, cfg), cfg)
    if not chunks:
        return 0

    # Single-instance guard: two rapid toggles can't double-speak.
    if not _acquire_single_instance():
        logging.getLogger("readaloud").info("another reader is running; exiting")
        return 0

    # Engine construction can raise (e.g. missing kokoro models, no say binary).
    # Build it inside a guard so the user gets a clean message, not a raw
    # traceback, and the single-instance pidfile is released.
    try:
        engine = _make_engine(cfg)
    except Exception as exc:
        _release_single_instance()
        print(f"readaloud: engine error: {exc}", file=sys.stderr)
        return 3

    def _handle_stop(signum, frame):  # noqa: ARG001
        engine.stop()

    def _handle_pause(signum, frame):  # noqa: ARG001
        engine.toggle_pause()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGHUP, _handle_stop)
    # SIGUSR1 toggles pause/resume. Its default disposition is to KILL the
    # process, so register the handler immediately after the engine exists,
    # before Hammerspoon can send the first toggle.
    signal.signal(signal.SIGUSR1, _handle_pause)

    try:
        engine.speak(chunks)
    except RuntimeError as exc:
        print(f"readaloud: {exc}", file=sys.stderr)
        return 1
    finally:
        _release_single_instance()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
