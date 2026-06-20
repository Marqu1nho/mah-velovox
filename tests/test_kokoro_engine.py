"""Tests for KokoroEngine model injection and _ensure_model() behavior."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import numpy as np
import pytest

from readaloud.config import DEFAULTS
from readaloud.engines.kokoro_engine import (
    _FIRST_HEAD_CHARS,
    KokoroEngine,
    _split_first_chunk,
)
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


# --- first-chunk split (fast time-to-first-word) ---------------------------


def test_split_first_chunk_long_commaless():
    """A long comma-less first chunk is split into a small head + remainder."""
    text = (
        "The quick brown fox jumps over the lazy dog while the morning sun "
        "rises slowly above the distant misty hills beyond the valley below"
    )
    assert len(text) > _FIRST_HEAD_CHARS
    original = Chunk(text, "paragraph", rate_factor=1.2, pause_after_ms=300)
    out = _split_first_chunk([original])

    assert len(out) == 2
    head, remainder = out
    # Head is small (clean boundary near the ~80 char target; allow slack).
    assert len(head.text) <= 90
    assert len(head.text) > 0
    # No text lost: head + remainder reconstitutes the original.
    assert (head.text + " " + remainder.text).split() == text.split()
    # Head flows into the remainder: no trailing pause, no leading silence.
    assert head.pause_after_ms == 0
    assert remainder.pause_before_ms == 0
    # Remainder keeps the original chunk's trailing pause and rate.
    assert remainder.pause_after_ms == 300
    assert head.rate_factor == 1.2
    assert remainder.rate_factor == 1.2


def test_split_first_chunk_short_unchanged():
    """A short first chunk is left untouched."""
    original = Chunk("hello world", "paragraph", pause_after_ms=200)
    out = _split_first_chunk([original])
    assert out == [original]
    assert len(out) == 1


def test_split_first_chunk_only_affects_first():
    """Only the first chunk is split; later chunks pass through unchanged."""
    long_text = "word " * 40  # ~200 chars, no sentence boundaries
    first = Chunk(long_text.strip(), "paragraph")
    second = Chunk("second chunk text", "paragraph", pause_after_ms=100)
    out = _split_first_chunk([first, second])
    assert out[-1] == second
    assert len(out) == 3  # head, remainder, untouched second


def test_split_first_chunk_empty_list():
    assert _split_first_chunk([]) == []


class RecordingKokoro:
    """Records the order of texts passed to .create."""

    def __init__(self):
        self.calls: list[str] = []

    def create(self, text, voice, speed, lang):
        self.calls.append(text)
        return np.zeros(2400, dtype=np.float32), 24000


class FakeStream:
    """No-op stand-in for an sd.OutputStream."""

    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def write(self, data):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def abort(self):
        pass


def test_speak_synthesizes_small_head_first(monkeypatch):
    """speak() synthesizes the small head FIRST, before the remainder."""
    import sys
    import types

    fake_sd = types.ModuleType("sounddevice")
    fake_sd.OutputStream = FakeStream
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    text = (
        "The quick brown fox jumps over the lazy dog while the morning sun "
        "rises slowly above the distant misty hills beyond the valley below"
    )
    model = RecordingKokoro()
    cfg = _cfg(**{"engine": "kokoro"})
    engine = KokoroEngine(cfg, model=model)
    engine.speak([Chunk(text, "paragraph")])

    # First synthesized unit is the small head, then the remainder.
    assert len(model.calls) >= 2
    assert len(model.calls[0]) <= 90
    assert model.calls[0] != text  # not the whole sentence first
    # Reconstitutes the original text.
    assert (model.calls[0] + " " + model.calls[1]).split() == text.split()
