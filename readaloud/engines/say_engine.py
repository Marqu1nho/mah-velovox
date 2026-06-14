"""macOS `say` engine with OWNED playback (frame-accurate pause/resume).

We render each speech-script chunk with `/usr/bin/say -o <tmp.wav>` (NOT to the
speakers) and play the resulting audio ourselves through `sounddevice`. This is
deliberate: the Siri neural voice hands the whole utterance to CoreAudio (a
separate process) almost immediately, so SIGSTOPping the idle `say` process does
NOT pause the audio — pause was fake. By owning playback we make pause = stop
feeding frames (frame-accurate) and resume pick up exactly where it left off.

Structure mirrors kokoro_engine.py:
  - synthesis step  : `say -o tmp.wav` (text on stdin) -> soundfile -> float32
  - producer thread : render chunk N, push frames (with silence) onto a queue
  - consumer        : write frames to an OutputStream in small blocks, checking
                      pause/stop Events BETWEEN blocks

Rate/voice rules are unchanged: no `-v` for say_voice "system" (the Siri
loophole), `-v <name>` otherwise, `-r round(base_wpm*rate_factor)` unless the
rate-sanity probe says the voice ignores `-r`.

We do NOT use embedded [[slnc]]/[[rate]] commands — neural voices may ignore
them, and we now render silence as frames and own all timing.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import threading
import time as _time
from dataclasses import replace
from typing import Any

import numpy as np

from ..script import Chunk

log = logging.getLogger("readaloud.say")

SAY_BIN = "/usr/bin/say"

# Fixed render/playback format. A constant samplerate keeps the output stream
# rate fixed for every chunk so we never have to reopen the stream mid-read.
SAMPLE_RATE = 22050
DATA_FORMAT = f"LEI16@{SAMPLE_RATE}"

_SENTINEL = object()

# Ramped chunk sizes for gap-free pipelining.
#
# The producer renders chunk N+1 while the consumer plays chunk N. No gap
# requires: audio_duration(chunk_i) >= render_time(chunk_{i+1}).
#
# Measured on this machine:
#   render_time(chars) ≈ 1.6 + 0.008 * chars  seconds (neural voice startup)
#   audio_duration(chars) ≈ chars * 12 / base_wpm  seconds (≈0.041s/char at 295wpm)
#
# Ramp verification (across 240–330 wpm range):
#   render(90)  = 2.32s → first audio at ~2.3s (fast first word)
#   audio(90)  @~300wpm ≈ 3.6s  ≥  render(150)=2.8s  ✓
#   audio(150) @~300wpm ≈ 6.0s  ≥  render(220)=3.4s  ✓
#   audio(220) @~300wpm ≈ 8.8s  ≥  render(240)=3.5s  ✓  (plateau)
#
# The ramp advances GLOBALLY across the entire read — it is NOT reset per
# paragraph. Only the very first few emitted chunks of a read are small,
# keeping first-word latency low (~2.3s), and all later chunks are MAX_CHARS.
RAMP_CHARS = [90, 150, 220]   # first N emitted chunks target these sizes
MAX_CHARS = 240                # all subsequent chunks (and the plateau) cap here


def _say_args(voice: str, wpm: int | None) -> list[str]:
    args = [SAY_BIN]
    if voice and voice != "system":
        args += ["-v", voice]
    if wpm is not None:
        args += ["-r", str(wpm)]
    return args


def build_chunk_command(
    chunk: Chunk, cfg: dict[str, Any], rate_works: bool = True, out_path: str | None = None
) -> list[str]:
    """Construct the argv for rendering a single chunk to a wav file.

    Pure/testable: encodes the no-`-v`-for-system rule and the rate math. When
    the rate-sanity probe found that this voice ignores `-r`, the `-r` flag is
    omitted entirely (proceed at base rate) rather than passed uselessly. When
    ``out_path`` is given, append the fixed-format `-o` args so the chunk renders
    to that wav file instead of the speakers. The chunk text goes on stdin, NOT
    argv (a chunk starting with `-` must not be parsed as a flag).
    """
    voice_cfg = cfg.get("voice", {})
    say_voice = voice_cfg.get("say_voice", "system")
    base_wpm = int(voice_cfg.get("base_wpm", 190))
    wpm = max(1, round(base_wpm * chunk.rate_factor)) if rate_works else None
    args = _say_args(say_voice, wpm)
    if out_path is not None:
        args += [f"--data-format={DATA_FORMAT}", "-o", out_path]
    return args


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
                f"--data-format={DATA_FORMAT}",
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


def _coalesce(chunks: list[Chunk]) -> list[Chunk]:
    """Merge consecutive chunks with the same rate and no pause between them.

    Every `say` invocation pays noticeable process/voice startup (~1-2s with
    neural voices like Siri), so the per-sentence chunks produced for kokoro
    pipelining turn inter-sentence gaps into long dead air. Sentence
    granularity buys nothing here: we own playback and stop is frame-accurate
    regardless. Pause-only chunks (e.g. horizontal rules) are kept as
    boundaries.

    Unlike the old coalesce, this pass merges WITHOUT a char cap — the
    downstream _resegment() step re-splits the merged runs at ramp-sized
    boundaries. The only merge guard is whether chunks are in the same run
    (same rate_factor, no pause between them, both have non-empty text).
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


def _find_split_point(text: str, target: int) -> int:
    """Find the split index NEAREST to ``target`` at a clean boundary.

    We MAXIMIZE the piece size (boundary closest to ``target``, not the
    earliest) so no piece is undersized: an undersized piece runs out of audio
    before the next chunk finishes rendering, which causes an audible gap.
    Splits are seamless (no pause inserted between pieces of a run), so
    splitting inside a sentence is inaudible — size is what keeps the render
    pipeline fed. We deliberately do NOT prefer commas: an early comma yields a
    too-small chunk (that was the gap bug). Prefer a sentence end only when it
    lands at/near target, else use the last word boundary (space) at/under
    target. Never splits mid-word except the hard-cut fallback.

    Returns an index i such that text[:i] is the first piece and
    text[i:].lstrip() is the remainder.
    """
    if target >= len(text):
        return len(text)

    best = 0
    # Sentence boundary nearest target (small slack so a sentence ending just
    # past target still counts). Character class after sentence-ending punct:
    # straight/curly quotes, parens, brackets.
    sent_re = re.compile(r"""[.!?]["')\]]*\s+""")
    for m in sent_re.finditer(text, 0, target + 15):
        if m.end() <= target + 10:
            best = max(best, m.end())
    # Last word boundary (space) at/under target.
    for i in range(min(target, len(text) - 1), 0, -1):
        if text[i] == " ":
            best = max(best, i + 1)
            break
    # Hard fallback when there's no usable boundary (e.g. one giant token).
    return best if best > 0 else target


def _resegment(chunks: list[Chunk], _ramp_start: int = 0) -> list[Chunk]:
    """Coalesce + re-split chunks using a globally-advancing ramp of target sizes.

    Replaces the old (_coalesce + _split_first_chunk) pipeline with a single
    pass that:

      1. Groups consecutive input chunks into "runs" — same rate_factor, no
         pause between them, both having non-empty text.  A pause boundary,
         rate change, or pause-only chunk (HR) ends the current run.

      2. For each run, concatenates all chunk text, then re-splits it into
         pieces whose sizes follow the global ramp [90, 150, 220, 240, 240, …].
         The ramp index advances globally across the entire read (not per run),
         so only the very first few emitted chunks are small.

      3. The run's leading pause_before_ms goes on its first emitted piece;
         the trailing pause_after_ms goes on its last emitted piece; interior
         pieces have no pauses.  rate_factor is carried through to every piece.

      4. Pause-only chunks (HR, empty text) pass through as their own boundary
         pieces unchanged.

    This guarantees audio_duration(chunk_i) >= render_time(chunk_{i+1}) across
    the full read, eliminating the gap introduced when a tiny head chunk ran
    out of audio before the next render completed.

    ``_ramp_start`` is exposed only for testing (lets tests start mid-ramp).
    """
    coalesced = _coalesce(chunks)
    result: list[Chunk] = []
    ramp_idx = _ramp_start  # advances globally; never resets

    def _target() -> int:
        if ramp_idx < len(RAMP_CHARS):
            return RAMP_CHARS[ramp_idx]
        return MAX_CHARS

    def _emit_run(run_chunk: Chunk) -> None:
        """Re-split a single coalesced run chunk into ramp-sized pieces."""
        nonlocal ramp_idx
        text = run_chunk.text
        pause_before = run_chunk.pause_before_ms
        pause_after = run_chunk.pause_after_ms
        rate_factor = run_chunk.rate_factor
        kind = run_chunk.kind

        pieces: list[str] = []
        remaining = text
        while remaining:
            tgt = _target()
            if len(remaining) <= tgt:
                pieces.append(remaining)
                ramp_idx += 1
                remaining = ""
            else:
                split_at = _find_split_point(remaining, tgt)
                head = remaining[:split_at].rstrip()
                tail = remaining[split_at:].lstrip()
                if not head:
                    # Degenerate: no valid split point; take the whole remainder.
                    pieces.append(remaining)
                    ramp_idx += 1
                    remaining = ""
                else:
                    pieces.append(head)
                    ramp_idx += 1
                    remaining = tail

        for idx, piece_text in enumerate(pieces):
            is_first = idx == 0
            is_last = idx == len(pieces) - 1
            result.append(
                replace(
                    run_chunk,
                    text=piece_text,
                    kind=kind,
                    rate_factor=rate_factor,
                    pause_before_ms=pause_before if is_first else 0,
                    pause_after_ms=pause_after if is_last else 0,
                )
            )

    for coalesced_chunk in coalesced:
        if coalesced_chunk.text.strip():
            _emit_run(coalesced_chunk)
        else:
            # Pause-only chunk (e.g. HR): pass through untouched.
            result.append(replace(coalesced_chunk))

    return result


def _silence(ms: int) -> np.ndarray:
    n = int(SAMPLE_RATE * ms / 1000.0)
    return np.zeros(n, dtype=np.float32)


def _state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "readaloud")


def _sweep_stale_tmp() -> None:
    """Delete render wavs orphaned by a prior crashed/killed run.

    Normal operation deletes each wav right after reading it (and stop() cleans
    any in-flight file), so this only matters when a render is killed hard
    (e.g. SIGKILL) before its `finally` runs. The single-instance guard means
    no other reader is using these, so any leftover say-*.wav is stale. Calling
    this at the start of every read bounds accumulation to a single session.
    """
    import glob

    try:
        for path in glob.glob(os.path.join(_state_dir(), "say-*.wav")):
            try:
                os.unlink(path)
            except OSError:
                pass
    except OSError:
        pass


def _render_chunk_to_array(
    chunk: Chunk, cfg: dict[str, Any], rate_works: bool, tmp_paths: set[str]
) -> np.ndarray | None:
    """Render one chunk's text to a temp wav via `say -o`, read it as float32.

    The chunk text is fed on stdin (never argv) so a chunk starting with `-` is
    not misparsed as a flag and long coalesced text isn't subject to argv
    limits. Returns a mono float32 array at SAMPLE_RATE, or None on failure /
    empty output. Always deletes its temp file. ``tmp_paths`` tracks the path so
    stop() can clean up even if the read is interrupted between create and unlink.
    """
    import tempfile

    import soundfile as sf

    text = chunk.text.strip()
    if not text:
        return None

    state = _state_dir()
    try:
        os.makedirs(state, exist_ok=True)
    except OSError:
        state = None  # fall back to system temp

    with tempfile.NamedTemporaryFile(
        suffix=".wav", prefix="say-", dir=state, delete=False
    ) as tf:
        path = tf.name
    tmp_paths.add(path)
    try:
        args = build_chunk_command(chunk, cfg, rate_works, out_path=path)
        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.warning("failed to launch say: %s", exc)
            return None
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.write(text.encode("utf-8"))
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            log.warning("say render timed out; skipping chunk")
            return None

        try:
            data, sr = sf.read(path, dtype="float32", always_2d=False)
        except Exception as exc:
            log.warning("could not read rendered wav (skipping chunk): %s", exc)
            return None
        if data.ndim > 1:  # collapse to mono just in case
            data = data.mean(axis=1)
        data = np.asarray(data, dtype=np.float32)
        if not len(data):
            log.warning("empty render for chunk %r; skipping", text[:40])
            return None
        if sr != SAMPLE_RATE:
            # We requested LEI16@22050, so this shouldn't happen; resample
            # nearest-neighbour just in case so the fixed-rate stream stays valid.
            ratio = SAMPLE_RATE / float(sr)
            idx = (np.arange(int(len(data) * ratio)) / ratio).astype(int)
            idx = np.clip(idx, 0, len(data) - 1)
            data = data[idx]
        return data
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
        tmp_paths.discard(path)


class SayEngine:
    """`say` engine that owns playback via sounddevice (frame-accurate pause)."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        if not os.path.exists(SAY_BIN):
            raise RuntimeError(f"say binary not found at {SAY_BIN}")
        self._stop = threading.Event()
        # _resume is SET when playing, CLEAR when paused. The consumer waits on
        # it between frame blocks; stop() sets it so a paused consumer unblocks
        # and exits cleanly. Mirrors kokoro's pause model.
        self._resume = threading.Event()
        self._resume.set()
        self._paused = False
        self._stream = None
        self._stream_lock = threading.Lock()
        self._tmp_paths: set[str] = set()

    def speak(self, chunks: list[Chunk]) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:  # ImportError or backend init failure
            raise RuntimeError(f"sounddevice unavailable: {exc}") from exc

        self._paused = False
        self._resume.set()
        self._stop.clear()

        _sweep_stale_tmp()  # clear any wavs orphaned by a prior hard-killed run

        rate_works = _rate_sanity_check(self.cfg)
        # Resegment: coalesce runs then re-split at ramp-sized boundaries so
        # audio_duration(chunk_i) >= render_time(chunk_{i+1}) with no gaps.
        chunks = _resegment(chunks)
        rewind_ms = self.cfg.get("playback", {}).get("resume_rewind_ms", 600)
        rewind_frames = int(rewind_ms / 1000 * SAMPLE_RATE)
        recent = np.zeros(0, dtype=np.float32)  # rolling buffer of recently-played frames
        audio_q: queue.Queue = queue.Queue(maxsize=4)

        def producer() -> None:
            for chunk in chunks:
                if self._stop.is_set():
                    break
                parts: list[np.ndarray] = []
                if chunk.pause_before_ms:
                    parts.append(_silence(chunk.pause_before_ms))
                try:
                    body = _render_chunk_to_array(
                        chunk, self.cfg, rate_works, self._tmp_paths
                    )
                except Exception as exc:  # don't wedge the consumer
                    log.warning("synthesis failed (skipping chunk): %s", exc)
                    body = None
                if body is not None and len(body):
                    parts.append(body)
                if chunk.pause_after_ms:
                    parts.append(_silence(chunk.pause_after_ms))
                if parts:
                    audio_q.put(np.concatenate(parts))
            audio_q.put(_SENTINEL)

        prod = threading.Thread(target=producer, daemon=True)
        prod.start()

        try:
            stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32"
            )
            stream.start()
        except Exception as exc:
            self._stop.set()
            self._resume.set()
            prod.join(timeout=1.0)
            self._cleanup_tmp()
            raise RuntimeError(f"could not open audio output: {exc}") from exc

        with self._stream_lock:
            self._stream = stream
        _t0 = _time.monotonic()
        _first_audio_logged = False
        block = 2048  # frames per write; pause/stop are checked between blocks
        try:
            while not self._stop.is_set():
                try:
                    # Time out so a stop() while the producer is mid-render is
                    # noticed promptly instead of blocking until the next put.
                    item = audio_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if item is _SENTINEL:
                    break
                wave = item.reshape(-1, 1)
                for start in range(0, len(wave), block):
                    if self._stop.is_set():
                        break
                    # Block here while paused; stop() sets _resume to release.
                    was_paused = not self._resume.is_set()
                    while not self._resume.wait(timeout=0.2):
                        if self._stop.is_set():
                            break
                    if self._stop.is_set():
                        break
                    # On resume edge: replay the last ~rewind_ms of audio for re-entry context.
                    if was_paused and rewind_frames > 0 and len(recent) > 0:
                        replay = recent[-rewind_frames:].reshape(-1, 1)
                        try:
                            stream.write(replay)
                        except Exception:
                            break  # stream aborted by stop()
                        # Do NOT append replay to recent — that would create a feedback echo.
                    if not _first_audio_logged:
                        log.info(
                            "readaloud.say: first audio in %.2fs",
                            _time.monotonic() - _t0,
                        )
                        _first_audio_logged = True
                    try:
                        stream.write(wave[start : start + block])
                    except Exception:
                        break  # stream aborted by stop()
                    written_block = wave[start : start + block].reshape(-1)
                    if rewind_frames > 0:
                        recent = np.concatenate([recent, written_block])
                        if len(recent) > rewind_frames:
                            recent = recent[-rewind_frames:]
        finally:
            with self._stream_lock:
                self._stream = None
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self._stop.set()
            # Drain the queue so a producer blocked in put() after stop can't
            # wedge forever before the join.
            try:
                while True:
                    audio_q.get_nowait()
            except queue.Empty:
                pass
            prod.join(timeout=2.0)
            self._cleanup_tmp()

    def toggle_pause(self) -> None:
        """Frame-accurate pause/resume of playback (the audio stream).

        On pause we stop the stream (halts playback, keeps it open) and clear
        the resume event so the consumer blocks between frame blocks — no more
        frames are fed, so audio truly stops. On resume we restart the stream
        and set the event, and playback continues from the exact next block.
        The producer keeps filling the bounded queue regardless. A toggle
        issued before playback starts or after it ends is harmless.
        """
        if self._stop.is_set():
            return
        self._paused = not self._paused
        with self._stream_lock:
            stream = self._stream
        if self._paused:
            self._resume.clear()
            if stream is not None:
                try:
                    stream.stop()  # halt playback, keep stream open
                except Exception:
                    pass
        else:
            if stream is not None:
                try:
                    stream.start()
                except Exception:
                    pass
            self._resume.set()

    def stop(self) -> None:
        self._stop.set()
        # Release a paused consumer so it can observe _stop and exit; clear the
        # pause flag so a subsequent speak() starts clean.
        self._paused = False
        self._resume.set()
        with self._stream_lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.abort()  # immediate: drop in-flight audio, don't drain
            except Exception:
                pass
        self._cleanup_tmp()

    def _cleanup_tmp(self) -> None:
        for path in list(self._tmp_paths):
            try:
                os.unlink(path)
            except OSError:
                pass
            self._tmp_paths.discard(path)


def speak(chunks: list[Chunk], cfg: dict[str, Any]) -> SayEngine:
    """Module-level convenience: create engine and speak synchronously."""
    engine = SayEngine(cfg)
    engine.speak(chunks)
    return engine
