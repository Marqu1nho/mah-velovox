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
from pathlib import Path
from typing import Any

import numpy as np

from ..script import Chunk

log = logging.getLogger("readaloud.kokoro")

SAMPLE_RATE = 24000  # kokoro-onnx output sample rate

_SENTINEL = object()


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
    def __init__(self, cfg: dict[str, Any]):
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
        self._kokoro = None
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
                    while not self._resume.wait(timeout=0.2):
                        if self._stop.is_set():
                            break
                    if self._stop.is_set():
                        break
                    try:
                        stream.write(wave[start : start + block])
                    except Exception:
                        break  # stream aborted by stop()
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
