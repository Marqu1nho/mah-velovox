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
    args = parser.parse_args(argv)

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
        cleaned = clean(raw, cfg)
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

    cleaned = clean(raw, cfg)
    chunks = build_script(parse(cleaned, cfg), cfg)
    if not chunks:
        return 0

    # Single-instance guard: two rapid toggles can't double-speak.
    if not _acquire_single_instance():
        logging.getLogger("readaloud").info("another reader is running; exiting")
        return 0

    engine = _make_engine(cfg)

    def _handle_stop(signum, frame):  # noqa: ARG001
        engine.stop()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

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
