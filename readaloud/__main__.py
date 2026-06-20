"""readaloud CLI entry point (Typer).

Commands:
    readaloud daemon                       run the warm daemon
    readaloud send read [--app NAME]       read stdin text via the daemon (blocks)
    readaloud send pause                   toggle pause via the daemon
    readaloud send stop                    stop via the daemon
    readaloud speak [--window] [--app NAME] [--config PATH]
                                           direct (non-daemon) playback from stdin
    readaloud config [--config PATH]       print merged config as JSON
    readaloud script [--app NAME] [--config PATH]
                                           print speech-script JSON from stdin
    readaloud --version
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Optional

import typer

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


def _direct_speak(window: bool, app: str | None, config: str | None) -> int:
    """Direct (non-daemon) playback from stdin: clean -> parse -> script -> engine.

    Honors the single-instance pidfile guard and installs signal handlers
    (SIGTERM/INT/HUP -> stop, SIGUSR1 -> toggle_pause).
    """
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        print(f"readaloud: config error: {exc}", file=sys.stderr)
        return 2

    raw = _read_stdin()
    if window:
        raw = _window_truncate(raw, cfg)
    else:
        raw = _truncate(raw, cfg)

    cleaned = clean(raw, cfg, app=app)
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


# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(
    help="Hotkey-triggered, markdown-aware text-to-speech reader for macOS.",
    no_args_is_help=True,
    add_completion=False,
)

send_app = typer.Typer(help="Relay a command to the warm daemon (lazy-starts it).")
app.add_typer(send_app, name="send")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"readaloud {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """readaloud — markdown-aware text-to-speech."""


@app.command()
def daemon() -> None:
    """Run the warm kokoro daemon."""
    from .daemon import main as daemon_main

    raise typer.Exit(code=daemon_main())


@send_app.command("read")
def send_read(
    app: Optional[str] = typer.Option(  # noqa: A002 - matches existing --app flag
        None, "--app", metavar="NAME", help="Frontmost app name, for per-app mute rules."
    ),
) -> None:
    """Read stdin text via the daemon; block until playback is done."""
    raise typer.Exit(code=_send_to_daemon("read", app))


@send_app.command("pause")
def send_pause() -> None:
    """Toggle pause via the daemon."""
    raise typer.Exit(code=_send_to_daemon("pause", None))


@send_app.command("stop")
def send_stop() -> None:
    """Stop playback via the daemon."""
    raise typer.Exit(code=_send_to_daemon("stop", None))


@app.command()
def speak(
    window: bool = typer.Option(
        False, "--window", help="Apply window_read.max_chars instead of max_selection_chars."
    ),
    app: Optional[str] = typer.Option(  # noqa: A002 - matches existing --app flag
        None, "--app", metavar="NAME", help="Frontmost app name, for per-app mute rules."
    ),
    config: Optional[str] = typer.Option(
        None, "--config", metavar="PATH", help="Config file path override."
    ),
) -> None:
    """Direct (non-daemon) playback from stdin."""
    raise typer.Exit(code=_direct_speak(window, app, config))


@app.command()
def config(  # noqa: A001 - command name is part of the contract
    config: Optional[str] = typer.Option(
        None, "--config", metavar="PATH", help="Config file path override."
    ),
) -> None:
    """Print the merged config as JSON."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        print(f"readaloud: config error: {exc}", file=sys.stderr)
        raise typer.Exit(code=2)
    json.dump(cfg, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


@app.command()
def script(
    app: Optional[str] = typer.Option(  # noqa: A002 - matches existing --app flag
        None, "--app", metavar="NAME", help="Frontmost app name, for per-app mute rules."
    ),
    config: Optional[str] = typer.Option(
        None, "--config", metavar="PATH", help="Config file path override."
    ),
) -> None:
    """Run clean+parse+script on stdin and print the chunks as JSON."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        print(f"readaloud: config error: {exc}", file=sys.stderr)
        raise typer.Exit(code=2)
    raw = _read_stdin()
    cleaned = clean(raw, cfg, app=app)
    chunks = build_script(parse(cleaned, cfg), cfg)
    json.dump([c.to_dict() for c in chunks], sys.stdout, indent=2)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Returns a process exit code.

    Kept callable with an ``argv`` list so tests can drive the CLI directly.
    Typer/Click raise SystemExit; we translate that back into a return code so
    callers that expect an int (and the ``[project.scripts]`` entry) both work.
    """
    # Typer vendors Click as ``typer._click`` (no top-level ``click`` dep).
    from typer import _click as click

    logging.basicConfig(
        level=logging.INFO,
        format="readaloud: %(message)s",
        stream=sys.stderr,
    )
    try:
        app(args=argv, standalone_mode=False)
    except click.exceptions.UsageError as exc:  # bad/missing args, no subcommand
        exc.show()
        return exc.exit_code if exc.exit_code is not None else 2
    except typer.Exit as exc:
        return exc.exit_code
    except SystemExit as exc:  # --help (exit 0) and other Click SystemExits
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
