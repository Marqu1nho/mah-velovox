"""speakwrite CLI entry point (Typer).

Commands:
    speakwrite config [--config PATH]              print merged config as JSON
    speakwrite stream [--config PATH] [--engine NAME]
                                                   stream from engine (mock only)
    speakwrite daemon                              run the warm parakeet daemon
    speakwrite send dictate                        stream STT via the daemon (blocks)
    speakwrite send stop                           stop the current dictation session
    speakwrite send ping                           ping the daemon
    speakwrite --version
"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import sys
import threading
from typing import Optional

import typer

from . import __version__
from .config import ConfigError, load_config
from .engines import make_engine
from .polish import polish
from .protocol import encode_done, encode_final, encode_partial


# ---------------------------------------------------------------------------
# Daemon client helpers
# ---------------------------------------------------------------------------

def _send_to_daemon(cmd: str) -> int:
    """Relay a command to the warm daemon over its unix socket.

    Lazy-starts the daemon (detached) if it isn't running, then issues the
    command.  ``dictate`` BLOCKS until the daemon reports done (streaming
    each line verbatim to stdout so the lua side can read them).
    ``stop``/``ping`` send and return immediately on ack.
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

    # Connect; if the socket is not yet listening, lazy-start the daemon
    # detached and poll until the socket accepts (generous 15s deadline for
    # model load + MLX warmup which can take several seconds).
    try:
        s = _connect()
    except OSError:
        logpath = _daemon.daemon_log_path()
        logpath.parent.mkdir(parents=True, exist_ok=True)
        logf = open(logpath, "ab")  # noqa: SIM115 (handed to the child)
        subprocess.Popen(
            [sys.executable, "-m", "speakwrite.daemon"],
            stdout=logf,
            stderr=logf,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 15.0  # model load + warmup; generous headroom
        s = None
        while time.monotonic() < deadline:
            try:
                s = _connect()
                break
            except OSError:
                time.sleep(0.1)
        if s is None:
            print("speakwrite: daemon did not become ready", file=sys.stderr)
            return 3

    try:
        if cmd == "dictate":
            s.sendall((json.dumps({"cmd": "dictate"}) + "\n").encode())
            rf = s.makefile("rb")
            while True:  # block until done / connection closed
                line = rf.readline()
                if not line:
                    break
                # Relay each line verbatim to stdout (lua reads these as streaming stdout).
                decoded = line.decode("utf-8", errors="replace")
                sys.stdout.write(decoded)
                sys.stdout.flush()
                try:
                    msg = json.loads(decoded.strip())
                except json.JSONDecodeError:
                    continue
                if msg.get("event") == "done":
                    break
        else:  # stop | ping
            s.sendall((json.dumps({"cmd": cmd}) + "\n").encode())
            ack_line = s.makefile("rb").readline()
            if ack_line:
                decoded = ack_line.decode("utf-8", errors="replace")
                sys.stdout.write(decoded)
                sys.stdout.flush()
    finally:
        try:
            s.close()
        except OSError:
            pass
    return 0


# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(
    help="Mic → streaming speech-to-text → paste at cursor.",
    no_args_is_help=True,
    add_completion=False,
)

send_app = typer.Typer(help="Relay a command to the warm daemon (lazy-starts it).")
app.add_typer(send_app, name="send")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"speakwrite {__version__}")
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
    """speakwrite — mic → streaming speech-to-text → paste at cursor."""


@app.command()
def daemon() -> None:
    """Run the warm parakeet daemon."""
    from .daemon import main as daemon_main

    raise typer.Exit(code=daemon_main())


@send_app.command("dictate")
def send_dictate() -> None:
    """Start a dictation session via the daemon; block until done."""
    raise typer.Exit(code=_send_to_daemon("dictate"))


@send_app.command("stop")
def send_stop() -> None:
    """Stop the current dictation session via the daemon."""
    raise typer.Exit(code=_send_to_daemon("stop"))


@send_app.command("ping")
def send_ping() -> None:
    """Ping the daemon."""
    raise typer.Exit(code=_send_to_daemon("ping"))


@app.command()
def config(  # noqa: A001 — command name is part of the contract
    config: Optional[str] = typer.Option(
        None, "--config", metavar="PATH", help="Config file path override."
    ),
) -> None:
    """Print the merged config as JSON."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        print(f"speakwrite: config error: {exc}", file=sys.stderr)
        raise typer.Exit(code=2)
    json.dump(cfg, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


@app.command()
def stream(
    config: Optional[str] = typer.Option(
        None, "--config", metavar="PATH", help="Config file path override."
    ),
    engine: Optional[str] = typer.Option(
        None, "--engine", metavar="NAME", help="Engine override (parakeet|apple|whisper|mock)."
    ),
) -> None:
    """Stream speech-to-text output as NDJSON to stdout."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        print(f"speakwrite: config error: {exc}", file=sys.stderr)
        raise typer.Exit(code=2)

    # --engine flag overrides cfg["engine"].
    if engine is not None:
        cfg = dict(cfg)
        cfg["engine"] = engine

    try:
        eng = make_engine(cfg)
    except RuntimeError as exc:
        print(f"speakwrite: engine error: {exc}", file=sys.stderr)
        raise typer.Exit(code=3)

    # Set up stop event and signal handlers.
    stop = threading.Event()

    def _handle_stop(signum, frame):  # noqa: ARG001
        stop.set()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    if cfg.get("engine") == "mock":
        # Mock path: empty queue with a sentinel — MockEngine ignores frames.
        # IMPORTANT: this branch must NOT import sounddevice or capture.mic.
        frames: queue.Queue = queue.Queue()
        frames.put(None)

        try:
            for partial in eng.stream(frames, stop):
                line = encode_partial(partial.text, partial.volatile)
                sys.stdout.write(line)
                sys.stdout.flush()
        except Exception as exc:
            print(f"speakwrite: stream error: {exc}", file=sys.stderr)
            raise typer.Exit(code=1)

    else:
        # Real mic path: open the microphone, check for silence on the first
        # ~1 s (macOS Tahoe returns all-zero audio on mic-permission denial),
        # then feed frames into the engine.
        from .capture.mic import MicCapture, looks_silent, mic_permission_status
        import numpy as np

        status = mic_permission_status()
        if status in ("denied", "restricted"):
            print(
                f"speakwrite: microphone access {status}; grant permission in "
                "System Settings → Privacy & Security → Microphone",
                file=sys.stderr,
            )
            raise typer.Exit(code=1)

        cap = MicCapture(
            sample_rate=getattr(eng, "sample_rate", 16000),
            blocksize=1024,
        )
        mic_q = cap.open()

        # Also hook SIGTERM/SIGINT to close the mic.
        _orig_stop = stop.is_set

        def _handle_stop_with_mic(signum, frame):  # noqa: ARG001
            stop.set()
            cap.close()

        signal.signal(signal.SIGTERM, _handle_stop_with_mic)
        signal.signal(signal.SIGINT, _handle_stop_with_mic)

        # Collect the first ~1 s of audio to check for silence.
        _silence_check_samples = getattr(eng, "sample_rate", 16000)
        _first_audio: list = []
        _first_audio_len = 0
        _silence_checked = False

        def _on_frame_silence_check(chunk):
            nonlocal _first_audio_len, _silence_checked
            if not _silence_checked:
                _first_audio.append(chunk)
                _first_audio_len += len(chunk)
                if _first_audio_len >= _silence_check_samples:
                    _silence_checked = True
                    first_block = np.concatenate(_first_audio)
                    if looks_silent(first_block):
                        logging.getLogger("speakwrite").error(
                            "mic returned silence — check microphone permission "
                            "for the controlling app (macOS Tahoe returns zeros "
                            "when access is denied without an error)"
                        )

        cap._on_frame = _on_frame_silence_check

        try:
            for partial in eng.stream(mic_q, stop):
                line = encode_partial(partial.text, partial.volatile)
                sys.stdout.write(line)
                sys.stdout.flush()
        except Exception as exc:
            print(f"speakwrite: stream error: {exc}", file=sys.stderr)
            raise typer.Exit(code=1)
        finally:
            cap.close()

    final_text = polish(eng.final(), cfg.get("polish", "punctuation"))
    sys.stdout.write(encode_final(final_text))
    sys.stdout.flush()
    sys.stdout.write(encode_done())
    sys.stdout.flush()


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
        format="speakwrite: %(message)s",
        stream=sys.stderr,
    )
    # Suppress noisy third-party logs that leak during model load.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        result = app(args=argv, standalone_mode=False)
    except click.exceptions.UsageError as exc:  # bad/missing args, no subcommand
        exc.show()
        return exc.exit_code if exc.exit_code is not None else 2
    except typer.Exit as exc:
        return exc.exit_code
    except SystemExit as exc:  # --help (exit 0) and other Click SystemExits
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    # With standalone_mode=False, typer.Exit inside a command causes app() to
    # return the exit code as an int rather than raising. Propagate it.
    if isinstance(result, int) and result != 0:
        return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
