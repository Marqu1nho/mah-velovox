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
from dataclasses import replace
from typing import Any

from ..script import Chunk

log = logging.getLogger("readaloud.say")

SAY_BIN = "/usr/bin/say"


def _say_args(voice: str, wpm: int | None) -> list[str]:
    args = [SAY_BIN]
    if voice and voice != "system":
        args += ["-v", voice]
    if wpm is not None:
        args += ["-r", str(wpm)]
    return args


def build_chunk_command(
    chunk: Chunk, cfg: dict[str, Any], rate_works: bool = True
) -> list[str]:
    """Construct the argv for a single chunk (the text goes on stdin, NOT argv).

    Pure/testable: encodes the no-`-v`-for-system rule and the rate math. When
    the rate-sanity probe found that this voice ignores `-r`, the `-r` flag is
    omitted entirely (proceed at base rate) rather than passed uselessly.
    """
    voice_cfg = cfg.get("voice", {})
    say_voice = voice_cfg.get("say_voice", "system")
    base_wpm = int(voice_cfg.get("base_wpm", 190))
    wpm = max(1, round(base_wpm * chunk.rate_factor)) if rate_works else None
    return _say_args(say_voice, wpm)


def _sanity_cache_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "readaloud", "say_rate_ok")


def _rate_sanity_check(cfg: dict[str, Any]) -> bool:
    """Sanity-check that `-r` audibly changes rate; return whether it works.

    We cannot truly measure audibility headlessly, so we verify the say
    binary exists and accepts `-r` by synthesizing two short clips to file
    at very different rates and comparing durations. Best-effort; never
    fails the run (spec §3.4). The returned bool is plumbed into
    build_chunk_command so a voice that ignores `-r` proceeds at base rate.

    Because each read is a short-lived process, the (~2s) check is run at most
    once per machine+voice and a positive result is cached so it never adds
    latency to subsequent reads.
    """
    if not os.path.exists(SAY_BIN):
        log.warning("say binary not found at %s; say engine unavailable", SAY_BIN)
        return True

    voice_cfg = cfg.get("voice", {})
    say_voice = voice_cfg.get("say_voice", "system")

    # Cache: skip if we've already validated this voice on this machine.
    cache = _sanity_cache_path()
    try:
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as fh:
                if say_voice in fh.read().splitlines():
                    return True
    except OSError:
        pass

    return _run_rate_check_and_cache(cfg, say_voice, cache)


def _run_rate_check_and_cache(cfg: dict[str, Any], say_voice: str, cache: str) -> bool:
    ok = True
    measured = False  # did we get two valid (non-zero) durations?
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
            measured = True
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
        return True

    # Cache only a real positive (two valid durations showing -r works). A
    # degenerate probe (both durations 0.0) is never cached as success, so it
    # gets re-probed next time instead of being silently locked in.
    if ok and measured:
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
    return ok


def _slnc_cache_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "readaloud", "say_slnc_ok")


def _render_duration(voice: str, text: str) -> float:
    """Render text to a temp wav (no audio) and return its duration in seconds."""
    import tempfile
    import wave

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        path = tf.name
    try:
        args = _say_args(voice, 200) + ["--data-format=LEI16@22050", "-o", path, text]
        subprocess.run(args, capture_output=True, timeout=15, check=False)
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _slnc_supported(cfg: dict[str, Any]) -> bool:
    """Does the active voice honor embedded [[slnc N]] commands?

    Neural voices may ignore embedded commands (spec §3.4) — or worse, read
    them aloud — so we probe by rendering the same phrase with two different
    silence values and checking that the duration delta matches the slnc
    delta (rules out the read-aloud case, which lengthens both clips equally).
    Result is cached per voice; delete the cache file to re-probe.
    """
    voice = cfg.get("voice", {}).get("say_voice", "system")
    cache = _slnc_cache_path()
    try:
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as fh:
                for line in fh.read().splitlines():
                    if line == f"{voice}=yes":
                        return True
                    if line == f"{voice}=no":
                        return False
    except OSError:
        pass

    ok = False
    try:
        d_short = _render_duration(voice, "ping [[slnc 1500]] pong")
        d_long = _render_duration(voice, "ping [[slnc 3000]] pong")
        if d_short > 0 and d_long > 0:
            ok = 1.2 <= (d_long - d_short) <= 1.8
    except Exception as exc:  # never fail the run; just fall back
        log.debug("slnc probe failed: %s", exc)
        ok = False

    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "a", encoding="utf-8") as fh:
            fh.write(f"{voice}={'yes' if ok else 'no'}\n")
    except OSError:
        pass
    return ok


def _coalesce_slnc(chunks: list[Chunk]) -> list[Chunk]:
    """Merge chunks across pause boundaries using inline [[slnc N]] silences.

    Used when the active voice honors embedded silence commands: everything
    at the same rate_factor becomes a single `say` invocation with pauses
    rendered inline, eliminating per-process voice startup between paragraphs
    and list items. Rate changes (headers) still split, since [[rate]] is not
    reliably honored. Text-less chunks (horizontal rules) merge as pure
    silence into whichever group they touch.
    """
    merged: list[Chunk] = []
    for chunk in chunks:
        prev = merged[-1] if merged else None
        if prev is None:
            merged.append(replace(chunk))
            continue
        prev_textless = not prev.text.strip()
        cur_textless = not chunk.text.strip()
        if prev_textless or cur_textless or prev.rate_factor == chunk.rate_factor:
            gap = prev.pause_after_ms + chunk.pause_before_ms
            parts = [prev.text.strip()]
            if gap > 0:
                parts.append(f"[[slnc {gap}]]")
            parts.append(chunk.text.strip())
            prev.text = " ".join(p for p in parts if p)
            prev.pause_after_ms = chunk.pause_after_ms
            if prev_textless:
                prev.rate_factor = chunk.rate_factor
        else:
            merged.append(replace(chunk))
    return merged


def _coalesce(chunks: list[Chunk]) -> list[Chunk]:
    """Merge consecutive chunks with the same rate and no pause between them.

    Every `say` invocation pays noticeable process/voice startup (~1-2s with
    neural voices like Siri), so the per-sentence chunks produced for kokoro
    pipelining turn inter-sentence gaps into long dead air. Sentence
    granularity buys nothing here: stop SIGTERMs `say` mid-utterance anyway.
    Pause-only chunks (e.g. horizontal rules) are kept as boundaries.
    """
    merged: list[Chunk] = []
    for chunk in chunks:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and prev.text.strip()
            and chunk.text.strip()
            and prev.rate_factor == chunk.rate_factor
            and prev.pause_after_ms == 0
            and chunk.pause_before_ms == 0
        ):
            prev.text = prev.text.rstrip() + " " + chunk.text.lstrip()
            prev.pause_after_ms = chunk.pause_after_ms
        else:
            merged.append(replace(chunk))
    return merged


class SayEngine:
    """Sequential `say` engine with an interruptible queue."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None
        self._stopped = False
        if shutil.which(SAY_BIN) is None and not os.path.exists(SAY_BIN):
            raise RuntimeError(f"say binary not found at {SAY_BIN}")

    def speak(self, chunks: list[Chunk]) -> None:
        rate_works = _rate_sanity_check(self.cfg)
        if _slnc_supported(self.cfg):
            chunks = _coalesce_slnc(chunks)
        else:
            chunks = _coalesce(chunks)
        for chunk in chunks:
            if self._stopped:
                break
            if chunk.pause_before_ms:
                self._sleep_ms(chunk.pause_before_ms)
            if self._stopped:
                break
            text = chunk.text.strip()
            if text:
                self._speak_chunk(chunk, rate_works)
            if self._stopped:
                break
            if chunk.pause_after_ms:
                self._sleep_ms(chunk.pause_after_ms)

    def _speak_chunk(self, chunk: Chunk, rate_works: bool = True) -> None:
        # The chunk text goes on stdin, never argv: slnc-coalescing makes chunk
        # text long (an entire paragraph or list as one invocation), and a
        # chunk starting with `-` would be misparsed as a flag on argv. `say`
        # reads stdin when given no text arguments.
        args = build_chunk_command(chunk, self.cfg, rate_works)
        try:
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.error("failed to launch say: %s", exc)
            return
        try:
            proc = self._proc
            if proc.stdin is not None:
                try:
                    proc.stdin.write(chunk.text.encode("utf-8"))
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            proc.wait()
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
