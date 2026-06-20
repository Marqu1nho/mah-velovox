"""ParakeetEngine — real-time STT via parakeet-mlx.

Model: mlx-community/parakeet-tdt-0.6b-v3 (Apple Silicon only).

Architecture mirrors kokoro_engine.py:
  - Module-level _load_model() with lazy imports (no top-level parakeet_mlx).
  - __init__ accepts an injected model for testing / daemon pre-warm.
  - _ensure_model() caches the singleton.
  - warmup() triggers the one-time MLX kernel compile so the user's first
    dictation doesn't eat ~4 s.
  - stream() feeds audio chunks into a transcribe_stream context manager,
    polling draft_tokens (the full rolling transcript each time — NOT
    incremental) and yielding Partial objects on change.
  - final() returns the last known full transcript (caller applies polish).
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Iterator

import numpy as np

from .base import Partial

_log = logging.getLogger("speakwrite.parakeet")

_MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v3"

# Default chunk duration in seconds.  The engine accumulates audio until it
# has this many samples, then calls add_audio once so parakeet can refine
# the rolling transcript.
_DEFAULT_CHUNK_S = 1.0


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _load_model(model_id: str = _MODEL_ID):
    """Load (and cache on disk via HuggingFace) the parakeet model.

    Lazy-imported so this module can be imported on Linux / without MLX.
    """
    from parakeet_mlx import from_pretrained  # type: ignore[import]
    return from_pretrained(model_id)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ParakeetEngine:
    """Streaming STT engine backed by parakeet-mlx.

    Args:
        cfg:   Merged speakwrite config dict (reads cfg["parakeet"]["chunk_s"]
               if present; otherwise defaults to 1.0 s).
        model: Pre-loaded parakeet model instance.  Pass None (default) for
               lazy loading on first use.
    """

    name = "parakeet"
    sample_rate = 16000

    def __init__(self, cfg: dict[str, Any], model=None) -> None:
        self._cfg = cfg
        self._model = model
        # Chunk size in seconds: cfg["parakeet"]["chunk_s"] → default 1.0.
        parakeet_cfg = cfg.get("parakeet", {})
        self._chunk_s: float = float(parakeet_cfg.get("chunk_s", _DEFAULT_CHUNK_S))
        self._chunk_samples: int = int(self.sample_rate * self._chunk_s)
        self._final_text: str = ""

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def _ensure_model(self):
        if self._model is None:
            _log.info("loading parakeet model %s …", _MODEL_ID)
            self._model = _load_model()
            _log.info("parakeet model loaded")
        return self._model

    def warmup(self) -> None:
        """Trigger the one-time MLX kernel compile with a silent dummy clip.

        Without this, the user's first real dictation eats ~4 s while MLX
        compiles the compute graph.  Call once at daemon startup (best-effort;
        exceptions are swallowed).
        """
        try:
            import mlx.core as mx  # type: ignore[import]
            model = self._ensure_model()
            dummy = np.zeros(self.sample_rate, dtype=np.float32)
            with model.transcribe_stream(context_size=(256, 256), depth=1) as tr:
                tr.add_audio(mx.array(dummy))
                _ = tr.draft_tokens  # touch it to ensure compile happens
            _log.info("parakeet warmup complete")
        except Exception as exc:
            _log.warning("parakeet warmup failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def stream(
        self,
        frames: "queue.Queue[np.ndarray | None]",
        stop: threading.Event,
    ) -> Iterator[Partial]:
        """Stream audio frames through parakeet, yielding rolling Partials.

        ``frames`` is a Queue of 1-D float32 numpy arrays at 16 kHz.
        A None sentinel signals end-of-stream; ``stop`` signals early abort.

        draft_tokens gives the FULL rolling transcript so far (not
        incremental — it revises itself as more audio arrives).  We yield a
        new Partial whenever the text changes.  Everything is volatile=True
        while streaming; the caller calls final() after stream() is exhausted.
        """
        import mlx.core as mx  # type: ignore[import]

        model = self._ensure_model()
        self._final_text = ""
        buffer: list[np.ndarray] = []
        buffer_len = 0
        last_text = ""

        def _flush_buffer(tr, buf: list[np.ndarray]) -> str:
            """Concatenate buf, feed to add_audio, return new draft text."""
            if not buf:
                return ""
            chunk = np.concatenate(buf, axis=0)
            try:
                tr.add_audio(mx.array(chunk))
            except Exception as exc:
                _log.error("add_audio error: %s", exc)
                return ""
            try:
                tokens = tr.draft_tokens
                return "".join(t.text for t in tokens).strip()
            except Exception as exc:
                _log.error("draft_tokens error: %s", exc)
                return ""

        try:
            with model.transcribe_stream(context_size=(256, 256), depth=1) as tr:
                while True:
                    # Drain the queue until we have a full chunk or a sentinel.
                    try:
                        frame = frames.get(timeout=0.1)
                    except queue.Empty:
                        if stop.is_set():
                            break
                        continue

                    if frame is None:
                        # End-of-stream sentinel — flush whatever is in the buffer.
                        if buffer:
                            text = _flush_buffer(tr, buffer)
                            buffer = []
                            buffer_len = 0
                            if text:
                                self._final_text = text
                                if text != last_text:
                                    last_text = text
                                    yield Partial(text, volatile=True)
                        break

                    buffer.append(frame)
                    buffer_len += len(frame)

                    if buffer_len >= self._chunk_samples:
                        # We have enough audio — feed it.
                        text = _flush_buffer(tr, buffer)
                        buffer = []
                        buffer_len = 0
                        if text and text != last_text:
                            last_text = text
                            self._final_text = text
                            yield Partial(text, volatile=True)

                    if stop.is_set():
                        # Flush remaining buffer before exiting.
                        if buffer:
                            text = _flush_buffer(tr, buffer)
                            buffer = []
                            buffer_len = 0
                            if text:
                                self._final_text = text
                                if text != last_text:
                                    last_text = text
                                    yield Partial(text, volatile=True)
                        break

        except Exception as exc:
            _log.error("parakeet stream error: %s", exc)

    # ------------------------------------------------------------------
    # Final transcript
    # ------------------------------------------------------------------

    def final(self) -> str:
        """Return the complete rolling transcript from the last stream() call.

        The caller (CLI / daemon) is responsible for applying polish.
        """
        return self._final_text
