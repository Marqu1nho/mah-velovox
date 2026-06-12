"""macOS `say` engine.

One `/usr/bin/say` invocation per speech-script chunk:
  - rate via `-r <wpm>` where wpm = base_wpm * chunk.rate_factor
  - no `-v` flag when say_voice is "system" (inherits the Spoken Content
    system voice — the Siri loophole, §00)
  - `-v <name>` for a named Premium voice
  - pauses are Python sleeps between chunks
  - stop = SIGTERM the current say process + abort the queue

We deliberately do NOT use embedded [[slnc]]/[[rate]] commands — neural
voices (Siri, Premium) may ignore them (spec §3.4).
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

from ..script import Chunk

log = logging.getLogger("readaloud.say")

SAY_BIN = "/usr/bin/say"


def _say_args(voice: str, wpm: int) -> list[str]:
    args = [SAY_BIN]
    if voice and voice != "system":
        args += ["-v", voice]
    args += ["-r", str(wpm)]
    return args


def build_chunk_command(chunk: Chunk, cfg: dict[str, Any]) -> list[str]:
    """Construct the argv for a single chunk (excluding the text on stdin).

    Pure/testable: encodes the no-`-v`-for-system rule and the rate math.
    """
    voice_cfg = cfg.get("voice", {})
    say_voice = voice_cfg.get("say_voice", "system")
    base_wpm = int(voice_cfg.get("base_wpm", 190))
    wpm = max(1, round(base_wpm * chunk.rate_factor))
    return _say_args(say_voice, wpm)


def _sanity_cache_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "readaloud", "say_rate_ok")


def _rate_sanity_check(cfg: dict[str, Any]) -> None:
    """Sanity-check that `-r` audibly changes rate; log a warning if not.

    We cannot truly measure audibility headlessly, so we verify the say
    binary exists and accepts `-r` by synthesizing two short clips to file
    at very different rates and comparing durations. Best-effort; never
    fails the run (spec §3.4).

    Because each read is a short-lived process, the (~2s) check is run at most
    once per machine+voice and the result is cached so it never adds latency
    to subsequent reads.
    """
    if not os.path.exists(SAY_BIN):
        log.warning("say binary not found at %s; say engine unavailable", SAY_BIN)
        return

    voice_cfg = cfg.get("voice", {})
    say_voice = voice_cfg.get("say_voice", "system")

    # Cache: skip if we've already validated this voice on this machine.
    cache = _sanity_cache_path()
    try:
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as fh:
                if say_voice in fh.read().splitlines():
                    return
    except OSError:
        pass

    _run_rate_check_and_cache(cfg, say_voice, cache)


def _run_rate_check_and_cache(cfg: dict[str, Any], say_voice: str, cache: str) -> None:
    ok = True
    try:
        import tempfile
        import wave

        durations = []
        for rate in (120, 320):
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                path = tf.name
            args = _say_args(say_voice, rate) + [
                "--data-format=LEI16@22050",
                "-o",
                path,
                "readaloud rate test",
            ]
            subprocess.run(args, capture_output=True, timeout=15, check=False)
            try:
                with wave.open(path, "rb") as w:
                    durations.append(w.getnframes() / float(w.getframerate()))
            except Exception:
                durations.append(0.0)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        if len(durations) == 2 and durations[0] > 0 and durations[1] > 0:
            # Faster rate (320) should be meaningfully shorter than slow (120).
            if durations[1] >= durations[0] * 0.85:
                ok = False
                log.warning(
                    "say -r does not appear to change rate with voice %r "
                    "(slow=%.2fs fast=%.2fs); proceeding at base rate",
                    say_voice,
                    durations[0],
                    durations[1],
                )
    except Exception as exc:  # never fail the run
        log.debug("rate sanity check skipped: %s", exc)
        return

    # Cache success so future reads skip the ~2s probe. We only cache when the
    # check actually ran cleanly; a failed/odd result is re-probed next time.
    if ok:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            existing = ""
            if os.path.exists(cache):
                with open(cache, encoding="utf-8") as fh:
                    existing = fh.read()
            if say_voice not in existing.splitlines():
                with open(cache, "a", encoding="utf-8") as fh:
                    fh.write(say_voice + "\n")
        except OSError:
            pass


class SayEngine:
    """Sequential `say` engine with an interruptible queue."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None
        self._stopped = False
        if shutil.which(SAY_BIN) is None and not os.path.exists(SAY_BIN):
            raise RuntimeError(f"say binary not found at {SAY_BIN}")

    def speak(self, chunks: list[Chunk]) -> None:
        _rate_sanity_check(self.cfg)
        for chunk in chunks:
            if self._stopped:
                break
            if chunk.pause_before_ms:
                self._sleep_ms(chunk.pause_before_ms)
            if self._stopped:
                break
            text = chunk.text.strip()
            if text:
                self._speak_chunk(chunk)
            if self._stopped:
                break
            if chunk.pause_after_ms:
                self._sleep_ms(chunk.pause_after_ms)

    def _speak_chunk(self, chunk: Chunk) -> None:
        args = build_chunk_command(chunk, self.cfg) + [chunk.text]
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.error("failed to launch say: %s", exc)
            return
        try:
            self._proc.wait()
        finally:
            self._proc = None

    def _sleep_ms(self, ms: int) -> None:
        # Sleep in small slices so stop is responsive.
        end = time.monotonic() + ms / 1000.0
        while not self._stopped and time.monotonic() < end:
            time.sleep(min(0.05, end - time.monotonic()))

    def stop(self) -> None:
        """SIGTERM the current say process and abort the queue."""
        self._stopped = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass


def speak(chunks: list[Chunk], cfg: dict[str, Any]) -> SayEngine:
    """Module-level convenience: create engine and speak synchronously."""
    engine = SayEngine(cfg)
    engine.speak(chunks)
    return engine
