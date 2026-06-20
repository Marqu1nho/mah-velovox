"""Tests for KokoroEngine model injection and _ensure_model() behavior."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import numpy as np
import pytest

from readaloud.config import DEFAULTS
from readaloud.engines.kokoro_engine import KokoroEngine
from readaloud.script import Chunk


def _cfg(**overrides):
    c = copy.deepcopy(DEFAULTS)
    for dotted, val in overrides.items():
        node = c
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = val
    return c


class FakeKokoro:
    """Minimal stand-in for a real Kokoro model."""

    def create(self, text, voice, speed, lang):
        # Return ~0.1s of silence at 24000 Hz.
        samples = np.zeros(2400, dtype=np.float32)
        return samples, 24000


def test_injected_model_reused_no_disk_load(monkeypatch, tmp_path):
    """When a model is injected, _load_kokoro is never called."""
    import readaloud.engines.kokoro_engine as ke

    monkeypatch.setattr(ke, "_load_kokoro", lambda: (_ for _ in ()).throw(AssertionError("_load_kokoro called!")))

    cfg = _cfg(**{"engine": "kokoro"})
    engine = KokoroEngine(cfg, model=FakeKokoro())
    out = tmp_path / "out.wav"
    frames = engine.synth_to_wav([Chunk("hello world", "paragraph")], str(out))
    assert frames > 0


def test_no_model_calls_load_kokoro(monkeypatch, tmp_path):
    """Without injection, _load_kokoro is called exactly once (lazy on first synth)."""
    import readaloud.engines.kokoro_engine as ke

    call_count = [0]
    original_fake = FakeKokoro()

    def counting_load():
        call_count[0] += 1
        return original_fake

    monkeypatch.setattr(ke, "_load_kokoro", counting_load)

    cfg = _cfg(**{"engine": "kokoro"})
    engine = KokoroEngine(cfg)
    out = tmp_path / "out.wav"
    engine.synth_to_wav([Chunk("hello world", "paragraph")], str(out))
    assert call_count[0] == 1


def test_ensure_model_idempotent(monkeypatch):
    """_ensure_model() called twice only loads once."""
    import readaloud.engines.kokoro_engine as ke

    call_count = [0]

    def counting_load():
        call_count[0] += 1
        return FakeKokoro()

    monkeypatch.setattr(ke, "_load_kokoro", counting_load)

    cfg = _cfg(**{"engine": "kokoro"})
    engine = KokoroEngine(cfg)
    engine._ensure_model()
    engine._ensure_model()
    assert call_count[0] == 1
