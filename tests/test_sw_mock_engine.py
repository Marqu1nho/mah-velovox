"""Tests for speakwrite/engines/mock.py — MockEngine behavior."""

import queue
import threading

import pytest

from speakwrite.engines.base import Partial
from speakwrite.engines.mock import MockEngine, _DEFAULT_SCRIPT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_stream(engine, stop=None):
    """Run engine.stream() to exhaustion, return list of Partials."""
    if stop is None:
        stop = threading.Event()
    frames: queue.Queue = queue.Queue()
    frames.put(None)
    return list(engine.stream(frames, stop))


# ---------------------------------------------------------------------------
# Default script
# ---------------------------------------------------------------------------


def test_default_script_yields_partials():
    eng = MockEngine()
    partials = _run_stream(eng)
    assert len(partials) == len(_DEFAULT_SCRIPT)


def test_default_script_order():
    eng = MockEngine()
    partials = _run_stream(eng)
    for got, expected in zip(partials, _DEFAULT_SCRIPT):
        assert got.text == expected.text
        assert got.volatile == expected.volatile


def test_default_script_starts_volatile():
    eng = MockEngine()
    partials = _run_stream(eng)
    assert partials[0].volatile is True


def test_default_script_has_committed_entries():
    eng = MockEngine()
    partials = _run_stream(eng)
    committed = [p for p in partials if not p.volatile]
    assert len(committed) >= 1


def test_volatile_to_committed_progression():
    """volatile=True items appear before volatile=False items for each sentence."""
    eng = MockEngine()
    partials = _run_stream(eng)
    # Find the first committed item.
    first_committed_idx = next(i for i, p in enumerate(partials) if not p.volatile)
    # Everything before it should be volatile.
    for p in partials[:first_committed_idx]:
        assert p.volatile is True


# ---------------------------------------------------------------------------
# final()
# ---------------------------------------------------------------------------


def test_final_returns_committed_text():
    eng = MockEngine()
    _run_stream(eng)
    final = eng.final()
    assert final  # non-empty
    # final() should be composed of committed (volatile=False) text.
    committed_texts = [p.text for p in _DEFAULT_SCRIPT if not p.volatile]
    for text in committed_texts:
        assert text in final


def test_final_before_stream_is_empty():
    eng = MockEngine()
    assert eng.final() == ""


def test_final_after_stop_is_partial():
    """If stop is set early, final() only has what was committed before stop."""
    stop = threading.Event()
    frames: queue.Queue = queue.Queue()
    frames.put(None)
    eng = MockEngine()
    partials = []
    for p in eng.stream(frames, stop):
        partials.append(p)
        if not p.volatile:  # stop after first committed item
            stop.set()
            break
    final = eng.final()
    # Only the first committed item should be in final().
    first_committed = next(p for p in _DEFAULT_SCRIPT if not p.volatile)
    assert first_committed.text in final


# ---------------------------------------------------------------------------
# stop Event
# ---------------------------------------------------------------------------


def test_stop_halts_stream():
    stop = threading.Event()
    stop.set()  # set before streaming
    eng = MockEngine()
    partials = _run_stream(eng, stop=stop)
    assert partials == []


def test_stop_mid_stream():
    """Set stop after first yield; should not get all items."""
    stop = threading.Event()
    frames: queue.Queue = queue.Queue()
    frames.put(None)
    eng = MockEngine()
    got = []
    for p in eng.stream(frames, stop):
        got.append(p)
        stop.set()  # stop after first yield
    assert len(got) == 1


# ---------------------------------------------------------------------------
# Custom script
# ---------------------------------------------------------------------------


def test_custom_script():
    script = [Partial("hello", True), Partial("hello world.", False)]
    eng = MockEngine(script=script)
    partials = _run_stream(eng)
    assert partials == script


def test_custom_script_final():
    script = [Partial("hello", True), Partial("hello world.", False)]
    eng = MockEngine(script=script)
    _run_stream(eng)
    assert "hello world." in eng.final()


def test_custom_empty_script():
    eng = MockEngine(script=[])
    partials = _run_stream(eng)
    assert partials == []
    assert eng.final() == ""


# ---------------------------------------------------------------------------
# frame_paced=False (default) — instant emit
# ---------------------------------------------------------------------------


def test_frame_paced_false_is_default():
    eng = MockEngine()
    import time
    start = time.monotonic()
    _run_stream(eng)
    elapsed = time.monotonic() - start
    # Should finish well under 1 second in instant mode.
    assert elapsed < 1.0


# ---------------------------------------------------------------------------
# Engine protocol compliance
# ---------------------------------------------------------------------------


def test_engine_has_name_and_sample_rate():
    eng = MockEngine()
    assert eng.name == "mock"
    assert eng.sample_rate == 16000


def test_partial_is_frozen():
    p = Partial("hello", True)
    with pytest.raises(Exception):
        p.text = "world"  # type: ignore[misc]


def test_partial_fields():
    p = Partial("test", False)
    assert p.text == "test"
    assert p.volatile is False
