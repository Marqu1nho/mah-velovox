"""Kokoro (local neural TTS) engine.

Producer thread synthesizes chunk-by-chunk; consumer plays from a queue via
sounddevice. Playback starts after the first chunk (low perceived latency).
Pauses are inserted as silent frames. Stop = threading.Event + stop stream.

Default voice af_heart, speed from config (× chunk rate_factor).

For headless verification (no speakers), use ``synth_to_wav`` which writes
the full synthesized output to a wav file without touching the audio device.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from ..script import Chunk
from .say_engine import _find_split_point

log = logging.getLogger("readaloud.kokoro")

SAMPLE_RATE = 24000  # kokoro-onnx output sample rate

_SENTINEL = object()

# Target size (chars) for the FIRST synthesized unit. The first chunk is split
# into a small head + remainder so the first synth completes quickly and
# playback starts sooner (time-to-first-word). kokoro synth is faster than
# realtime, so a small head causes no render-starvation gap. Subsequent chunks
# synthesize ahead during playback and are left untouched.
_FIRST_HEAD_CHARS = 80


def _split_first_chunk(chunks: list[Chunk]) -> list[Chunk]:
    """Split the FIRST chunk into a small head + remainder for fast first word.

    The head targets ``_FIRST_HEAD_CHARS`` chars, broken at a clean boundary
    (reusing ``say_engine._find_split_point``, which keeps punctuation attached
    and never splits mid-word except as a hard fallback). The head flows
    seamlessly into the remainder, so:
      - head: rate_factor and pause_before_ms carried; pause_after_ms = 0.
      - remainder: keeps the original chunk's pause_after_ms and rate_factor;
        pause_before_ms = 0 (no silence inserted between head and remainder).

    Only the first chunk is touched. Returns the list unchanged when there are
    no chunks, the first chunk's text is already <= target, or there is no
    clean split point.
    """
    if not chunks:
        return chunks
    first = chunks[0]
    text = first.text
    if len(text.strip()) <= _FIRST_HEAD_CHARS:
        return chunks
    split_at = _find_split_point(text, _FIRST_HEAD_CHARS)
    head = text[:split_at].rstrip()
    tail = text[split_at:].lstrip()
    if not head or not tail:
        # No clean split point (or nothing left over) — leave it unchanged.
        return chunks
    head_chunk = replace(first, text=head, pause_after_ms=0)
    tail_chunk = replace(first, text=tail, pause_before_ms=0)
    return [head_chunk, tail_chunk, *chunks[1:]]


def model_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "readaloud" / "models"
    return Path.home() / ".local" / "share" / "readaloud" / "models"


def model_paths() -> tuple[Path, Path]:
    d = model_dir()
    return d / "kokoro-v1.0.onnx", d / "voices-v1.0.bin"


def _load_kokoro():
    from kokoro_onnx import Kokoro

    onnx_path, voices_path = model_paths()
    if not onnx_path.exists() or not voices_path.exists():
        raise RuntimeError(
            f"Kokoro model files not found in {model_dir()}. "
            "Run install.sh (without --no-kokoro) to download them."
        )
    return Kokoro(str(onnx_path), str(voices_path))


def _silence(ms: int) -> np.ndarray:
    n = int(SAMPLE_RATE * ms / 1000.0)
    return np.zeros(n, dtype=np.float32)


def _synth_chunk(kokoro, chunk: Chunk, voice: str, base_speed: float) -> np.ndarray:
    """Synthesize a single chunk into a float32 mono waveform with pauses."""
    parts: list[np.ndarray] = []
    if chunk.pause_before_ms:
        parts.append(_silence(chunk.pause_before_ms))
    text = chunk.text.strip()
    if text:
        speed = base_speed * chunk.rate_factor
        samples, sr = kokoro.create(text, voice=voice, speed=speed, lang="en-us")
        samples = np.asarray(samples, dtype=np.float32)
        if sr != SAMPLE_RATE and len(samples):
            # kokoro-onnx returns 24k; guard just in case.
            ratio = SAMPLE_RATE / float(sr)
            idx = (np.arange(int(len(samples) * ratio)) / ratio).astype(int)
            idx = np.clip(idx, 0, len(samples) - 1)
            samples = samples[idx]
        parts.append(samples)
    if chunk.pause_after_ms:
        parts.append(_silence(chunk.pause_after_ms))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts)


class KokoroEngine:
    def __init__(self, cfg: dict[str, Any], model=None):
        # ``model`` lets the daemon inject a pre-loaded (warm) Kokoro instance so
        # it isn't reloaded from disk per read; None falls back to lazy load.
        self.cfg = cfg
        voice_cfg = cfg.get("voice", {})
        self.voice = voice_cfg.get("kokoro_voice", "af_heart")
        self.speed = float(voice_cfg.get("speed", 1.1))
        self._stop = threading.Event()
        # _resume is SET when playing, CLEAR when paused. The consumer waits
        # on it between frame blocks; stop() sets it so a paused consumer
        # unblocks and exits cleanly.
        self._resume = threading.Event()
        self._resume.set()
        self._paused = False
        self._kokoro = model
        self._stream = None
        self._stream_lock = threading.Lock()

    def _ensure_model(self):
        if self._kokoro is None:
            self._kokoro = _load_kokoro()
        return self._kokoro

    def speak(self, chunks: list[Chunk]) -> None:
        import sounddevice as sd

        self._paused = False
        self._resume.set()
        kokoro = self._ensure_model()
        # Split the first chunk into a small head + remainder so the first
        # synth completes quickly and playback starts sooner (time-to-first-word).
        chunks = _split_first_chunk(chunks)
        audio_q: queue.Queue = queue.Queue(maxsize=4)

        def producer() -> None:
            for chunk in chunks:
                if self._stop.is_set():
                    break
                try:
                    wave = _synth_chunk(kokoro, chunk, self.voice, self.speed)
                except Exception as exc:  # don't wedge the consumer
                    log.error("synthesis failed: %s", exc)
                    continue
                if len(wave):
                    audio_q.put(wave)
            audio_q.put(_SENTINEL)

        prod = threading.Thread(target=producer, daemon=True)
        prod.start()

        stream = sd.OutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32"
        )
        with self._stream_lock:
            self._stream = stream
        stream.start()
        rewind_ms = self.cfg.get("playback", {}).get("resume_rewind_ms", 600)
        rewind_frames = int(rewind_ms / 1000 * SAMPLE_RATE)
        recent = np.zeros(0, dtype=np.float32)
        try:
            while not self._stop.is_set():
                try:
                    # Time out so a stop() while the producer is mid-synthesis
                    # is noticed promptly instead of blocking until the next put.
                    item = audio_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if item is _SENTINEL:
                    break
                # Write in blocks so stop is responsive.
                wave = item.reshape(-1, 1)
                block = SAMPLE_RATE // 4
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
                    if was_paused and rewind_frames > 0 and len(recent) > 0:
                        replay = recent[-rewind_frames:].reshape(-1, 1)
                        try:
                            stream.write(replay)
                        except Exception:
                            break
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
            prod.join(timeout=1.0)

    def toggle_pause(self) -> None:
        """Best-effort pause/resume of playback (the audio stream).

        On pause we stop the stream (halts playback, keeps it open) and clear
        the resume event so the consumer blocks between frame blocks. On
        resume we restart the stream and set the event. The producer keeps
        filling the bounded queue regardless.
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
        # Release a paused consumer so it can observe _stop and exit; clear
        # the pause flag so a subsequent speak() starts clean.
        self._paused = False
        self._resume.set()
        with self._stream_lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.abort()  # immediate: drop in-flight audio, don't drain
            except Exception:
                pass

    def synth_to_wav(self, chunks: list[Chunk], out_path: str) -> int:
        """Synthesize all chunks to a wav file (no audio device).

        Returns the total number of frames written. Used for headless tests.
        """
        import soundfile as sf

        kokoro = self._ensure_model()
        parts: list[np.ndarray] = []
        for chunk in chunks:
            parts.append(_synth_chunk(kokoro, chunk, self.voice, self.speed))
        if parts:
            full = np.concatenate(parts)
        else:
            full = np.zeros(0, dtype=np.float32)
        sf.write(out_path, full, SAMPLE_RATE)
        return len(full)


def speak(chunks: list[Chunk], cfg: dict[str, Any]) -> KokoroEngine:
    engine = KokoroEngine(cfg)
    engine.speak(chunks)
    return engine
