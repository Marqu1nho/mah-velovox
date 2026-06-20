"""Integration test for ParakeetEngine.

Guarded so it only runs when:
  1. parakeet_mlx is importable.
  2. The model weights are already cached locally (no internet download in CI).

The test synthesizes a known English phrase via `say` (macOS TTS), resamples
it to 16 kHz mono float32, pushes it through ParakeetEngine.stream() in ~1 s
chunks, and asserts the transcript contains expected words.

This test is intentionally slow (it loads the model and runs inference).
"""

from __future__ import annotations

import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np
import pytest

# Guard 1: parakeet_mlx must be importable.
pytest.importorskip("parakeet_mlx")

# Guard 2: model weights must be cached locally.
_MODEL_CACHE = Path.home() / ".cache" / "huggingface" / "hub" / "models--mlx-community--parakeet-tdt-0.6b-v3"
if not _MODEL_CACHE.exists():
    pytest.skip(
        f"parakeet model weights not cached at {_MODEL_CACHE}; skipping integration test",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helper: synthesize a wav clip via macOS `say`, resample to 16 kHz mono
# ---------------------------------------------------------------------------


def _synthesize_clip(text: str, out_path: str) -> None:
    """Use macOS `say` to write an AIFF, then convert to 16 kHz mono wav."""
    aiff = out_path + ".aiff"
    subprocess.run(
        ["say", "-o", aiff, text],
        check=True,
        timeout=30,
    )
    # Convert to 16 kHz mono wav using afconvert (available on all macOS).
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEF32@16000", "-c", "1", aiff, out_path],
        check=True,
        timeout=30,
    )


def _load_wav_float32(path: str) -> np.ndarray:
    """Load a wav file and return a 1-D float32 array at the file's native rate.

    We use soundfile here because it's already in the project deps.
    The clip was generated at 16 kHz by afconvert, so no resampling needed.
    """
    import soundfile as sf  # type: ignore[import]
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data[:, 0]
    # Verify sample rate is 16 kHz as afconvert should produce.
    if sr != 16000:
        # Resample if for some reason afconvert gave us a different rate.
        import librosa  # type: ignore[import]
        data = librosa.resample(data, orig_sr=sr, target_sr=16000)
    return data.astype(np.float32)


# ---------------------------------------------------------------------------
# Actual test
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_parakeet_engine_transcribes_known_clip():
    """ParakeetEngine produces a transcript containing the spoken words."""
    from speakwrite.engines.parakeet import ParakeetEngine

    # We say a simple, clearly enunciated sentence parakeet handles well.
    test_phrase = "the quick brown fox"
    expected_words = ["quick", "fox"]  # must appear (case-insensitive)

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "test_clip.wav")
        try:
            _synthesize_clip(test_phrase, wav_path)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            pytest.skip(f"could not synthesize test clip: {exc}")

        audio = _load_wav_float32(wav_path)
        assert len(audio) > 0, "synthesized clip is empty"

    # Build a minimal config.
    cfg = {"engine": "parakeet", "polish": "punctuation"}
    eng = ParakeetEngine(cfg)

    # Chunk audio into ~1 s blocks and push into the queue.
    chunk_samples = 16000  # 1 s at 16 kHz
    frames: queue.Queue = queue.Queue()

    for start in range(0, len(audio), chunk_samples):
        chunk = audio[start : start + chunk_samples]
        frames.put(chunk.copy())
    frames.put(None)  # end-of-stream sentinel

    stop = threading.Event()
    partials = list(eng.stream(frames, stop))

    # --- assertions ---
    final = eng.final()
    assert final, "final() is empty — no transcription produced"

    final_lower = final.lower()
    for word in expected_words:
        assert word in final_lower, (
            f"expected word {word!r} not found in transcript {final!r}"
        )

    # All yielded partials must be Partial objects with volatile=True.
    from speakwrite.engines.base import Partial
    assert len(partials) >= 1, "stream() yielded no partials"
    for p in partials:
        assert isinstance(p, Partial)
        assert p.volatile is True


# ---------------------------------------------------------------------------
# Lightweight unit: final() before stream() returns empty string
# ---------------------------------------------------------------------------


def test_parakeet_engine_final_before_stream():
    """final() before any stream() call returns empty string."""
    from speakwrite.engines.parakeet import ParakeetEngine
    eng = ParakeetEngine({"engine": "parakeet", "polish": "none"}, model=object())
    # model=object() prevents actual loading; final() should still work.
    assert eng.final() == ""
