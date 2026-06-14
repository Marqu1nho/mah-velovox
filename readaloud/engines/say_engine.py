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

# Coalesce cap: bounds render time per chunk so no single say invocation is huge.
# A neural voice costs ~1.6s startup + ~0.008s/char, so 220 chars ≈ 3.3s render.
# After the first chunk, a chunk's audio (~44s of text at 190 wpm) covers the
# next render (~3.3s), so no audible gap opens between subsequent chunks.
MAX_COALESCE_CHARS = 220

# First-chunk head target: the head renders in ~1.6 + 90*0.008 ≈ 2.3s (first
# word audible at ~2.3s). The head's audio (~90 chars ≈ 18 words ≈ 3.6s at
# 190 wpm) covers the next chunk's render (~3.3s for 220 chars), so no gap
# opens after the head finishes. Only the FIRST chunk of a read is split this
# way; subsequent chunks are capped by MAX_COALESCE_CHARS.
FIRST_CHUNK_CHARS = 90


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

    Merged chunks are capped at MAX_COALESCE_CHARS so no single render is huge
    and first-audio latency stays bounded. The cap never splits a word.
    """
    merged: list[Chunk] = []
    for chunk in chunks:
        prev = merged[-1] if merged else None
        candidate = prev.text.rstrip() + " " + chunk.text.lstrip() if prev else ""
        if (
            prev is not None
            and prev.text.strip()
            and chunk.text.strip()
            and prev.rate_factor == chunk.rate_factor
            and prev.pause_after_ms == 0
            and chunk.pause_before_ms == 0
            and len(candidate) <= MAX_COALESCE_CHARS
        ):
            prev.text = candidate
            prev.pause_after_ms = chunk.pause_after_ms
        else:
            merged.append(replace(chunk))
    return merged


def _split_first_chunk(chunk: Chunk) -> list[Chunk]:
    """Split the first rendered chunk into a short head + remainder.

    The head targets FIRST_CHUNK_CHARS characters, broken at the best natural
    boundary at or under the target:
      1. End of first sentence (if already <= FIRST_CHUNK_CHARS chars).
      2. Last comma or semicolon boundary at/under the target.
      3. Last word boundary (space) at/under the target.
      4. The target index itself (fallback, avoids an infinite loop).

    If the chunk text is already <= FIRST_CHUNK_CHARS, it is returned as-is
    (no split needed). The head gets pause_after_ms=0; the remainder inherits
    the original chunk's rate_factor and trailing pause. No silence is
    inserted — they play back-to-back seamlessly.
    """
    text = chunk.text
    if len(text) <= FIRST_CHUNK_CHARS:
        return [chunk]

    target = FIRST_CHUNK_CHARS
    head_end: int | None = None

    # Prefer a sentence boundary (end of first sentence) if it is at/under target.
    # Character class: straight/curly quotes, parens, brackets after sentence punctuation.
    sent_re = re.compile(r"""[.!?]["')\]“”‘’]*\s+""")
    for m in sent_re.finditer(text):
        # end is the index AFTER the sentence-closing punctuation + trailing space
        # We want head = text[:m.end()] stripped; check if that's <= target.
        candidate_end = m.end()
        if candidate_end <= target + 10:  # small slack for sentence boundary pref
            head_end = candidate_end
            break  # first sentence boundary at/near target wins

    if head_end is None:
        # Prefer last comma/semicolon at/under target.
        for i in range(target, 0, -1):
            if text[i] in (",", ";"):
                head_end = i + 1
                break

    if head_end is None:
        # Fall back to last word boundary (space) at/under target.
        for i in range(target, 0, -1):
            if text[i] == " ":
                head_end = i + 1
                break

    if head_end is None:
        head_end = target  # hard fallback

    head_text = text[:head_end].rstrip()
    rest_text = text[head_end:].lstrip()

    if not head_text or not rest_text:
        # Degenerate split — return whole chunk unchanged.
        return [chunk]

    head = replace(chunk, text=head_text, pause_after_ms=0)
    remainder = replace(chunk, text=rest_text)  # keeps original pause_after_ms and rate_factor
    return [head, remainder]


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
        chunks = _coalesce(chunks)
        # Split the first chunk into a small head so first audio arrives quickly.
        if chunks:
            first_parts = _split_first_chunk(chunks[0])
            if len(first_parts) > 1:
                chunks = first_parts + chunks[1:]
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
