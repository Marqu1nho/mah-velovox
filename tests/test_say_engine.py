"""Tests for the say engine (command construction, coalesce, synthesis, pause/stop).

No real audio device is used: synthesis renders to a temp wav via `say -o`, and
pause/stop are tested on the threading Events (the audio stream is mocked or
absent). Audible pause/resume must be verified live (the orchestrator measures
whether a mid-read pause extends total wall-clock).
"""

import copy
import os

import numpy as np
import pytest

from readaloud.config import DEFAULTS
from readaloud.engines.say_engine import (
    SAMPLE_RATE,
    SAY_BIN,
    SayEngine,
    _coalesce,
    _render_chunk_to_array,
    _silence,
    build_chunk_command,
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


# ---------------------------------------------------------------------------
# build_chunk_command: voice + rate rules, and the new -o/--data-format args
# ---------------------------------------------------------------------------


def test_system_voice_has_no_v_flag():
    cfg = _cfg(**{"voice.say_voice": "system"})
    cmd = build_chunk_command(Chunk(text="hi", kind="paragraph"), cfg)
    assert "-v" not in cmd
    assert cmd[0] == "/usr/bin/say"


def test_named_voice_adds_v_flag():
    cfg = _cfg(**{"voice.say_voice": "Zoe (Premium)"})
    cmd = build_chunk_command(Chunk(text="hi", kind="paragraph"), cfg)
    assert "-v" in cmd
    assert cmd[cmd.index("-v") + 1] == "Zoe (Premium)"


def test_rate_is_base_times_rate_factor():
    cfg = _cfg(**{"voice.base_wpm": 200})
    chunk = Chunk(text="hi", kind="header", rate_factor=0.85)
    cmd = build_chunk_command(chunk, cfg)
    assert "-r" in cmd
    rate = int(cmd[cmd.index("-r") + 1])
    assert rate == round(200 * 0.85)  # 170


def test_paragraph_rate_is_base():
    cfg = _cfg(**{"voice.base_wpm": 190})
    chunk = Chunk(text="hi", kind="paragraph", rate_factor=1.0)
    cmd = build_chunk_command(chunk, cfg)
    rate = int(cmd[cmd.index("-r") + 1])
    assert rate == 190


def test_rate_omitted_when_probe_says_rate_does_not_work():
    cfg = _cfg(**{"voice.say_voice": "system"})
    chunk = Chunk(text="hi", kind="header", rate_factor=0.85)
    cmd = build_chunk_command(chunk, cfg, rate_works=False)
    assert "-r" not in cmd


def test_out_path_adds_data_format_and_o():
    cfg = _cfg(**{"voice.say_voice": "system"})
    cmd = build_chunk_command(
        Chunk(text="hi", kind="paragraph"), cfg, out_path="/tmp/x.wav"
    )
    assert f"--data-format=LEI16@{SAMPLE_RATE}" in cmd
    assert cmd[cmd.index("-o") + 1] == "/tmp/x.wav"


def test_no_out_path_has_no_o_flag():
    cfg = _cfg(**{"voice.say_voice": "system"})
    cmd = build_chunk_command(Chunk(text="hi", kind="paragraph"), cfg)
    assert "-o" not in cmd
    assert not any(a.startswith("--data-format") for a in cmd)


# ---------------------------------------------------------------------------
# _coalesce (no slnc): merge same-rate, no-pause-between chunks
# ---------------------------------------------------------------------------


def test_coalesce_merges_sentences_without_pauses():
    chunks = [
        Chunk(text="One.", kind="paragraph"),
        Chunk(text="Two.", kind="paragraph"),
        Chunk(text="Three.", kind="paragraph", pause_after_ms=350),
    ]
    out = _coalesce(chunks)
    assert len(out) == 1
    assert out[0].text == "One. Two. Three."
    assert out[0].pause_after_ms == 350


def test_coalesce_respects_rate_and_pause_boundaries():
    chunks = [
        Chunk(
            text="Header",
            kind="header",
            rate_factor=0.85,
            pause_before_ms=500,
            pause_after_ms=400,
        ),
        Chunk(text="Body one.", kind="paragraph"),
        Chunk(text="Body two.", kind="paragraph", pause_after_ms=350),
        Chunk(text="Next para.", kind="paragraph", pause_after_ms=350),
    ]
    out = _coalesce(chunks)
    assert [c.text for c in out] == ["Header", "Body one. Body two.", "Next para."]


def test_coalesce_keeps_pause_only_chunks_and_input_unmutated():
    chunks = [
        Chunk(text="One.", kind="paragraph"),
        Chunk(text="", kind="hr", pause_after_ms=600),
        Chunk(text="Two.", kind="paragraph", pause_after_ms=350),
    ]
    out = _coalesce(chunks)
    assert [c.kind for c in out] == ["paragraph", "hr", "paragraph"]
    assert chunks[0].text == "One."


# ---------------------------------------------------------------------------
# silence frames
# ---------------------------------------------------------------------------


def test_silence_is_zeros_at_samplerate():
    sil = _silence(1000)
    assert sil.dtype == np.float32
    assert len(sil) == SAMPLE_RATE  # 1000 ms == 1 second of frames
    assert not sil.any()


# ---------------------------------------------------------------------------
# synthesis to array (renders via `say -o`; no speakers). Skips without say.
# ---------------------------------------------------------------------------


def test_render_chunk_to_array_produces_float32_at_samplerate():
    if not os.path.exists(SAY_BIN):
        pytest.skip("say binary not present")
    cfg = _cfg(**{"voice.say_voice": "system"})
    tmp_paths: set = set()
    chunk = Chunk(text="readaloud synthesis test one two three.", kind="paragraph")
    data = _render_chunk_to_array(chunk, cfg, rate_works=True, tmp_paths=tmp_paths)
    assert data is not None
    assert data.dtype == np.float32
    assert len(data) > SAMPLE_RATE * 0.3  # at least ~0.3s of audio
    # Plausible duration: a short phrase shouldn't be more than ~10s.
    assert len(data) < SAMPLE_RATE * 10
    # No temp file leaked.
    assert not tmp_paths
    assert not any(os.path.exists(p) for p in tmp_paths)


def test_render_chunk_empty_text_returns_none():
    cfg = _cfg(**{"voice.say_voice": "system"})
    tmp_paths: set = set()
    data = _render_chunk_to_array(
        Chunk(text="   ", kind="paragraph"), cfg, rate_works=True, tmp_paths=tmp_paths
    )
    assert data is None
    assert not tmp_paths


# ---------------------------------------------------------------------------
# Pause / stop state transitions (no real device: stream stays None)
# ---------------------------------------------------------------------------


def test_toggle_pause_flips_state_and_resume_event():
    eng = SayEngine(_cfg())
    assert eng._paused is False
    assert eng._resume.is_set()  # SET == playing
    eng.toggle_pause()
    assert eng._paused is True
    assert not eng._resume.is_set()  # CLEAR == paused, consumer blocks
    eng.toggle_pause()
    assert eng._paused is False
    assert eng._resume.is_set()


def test_toggle_pause_noop_after_stop():
    eng = SayEngine(_cfg())
    eng.stop()
    eng.toggle_pause()
    assert eng._paused is False  # stop() locks pause out
    assert eng._stop.is_set()


def test_stop_releases_paused_consumer():
    """stop() must set _stop and re-set _resume so a paused consumer unblocks."""
    eng = SayEngine(_cfg())
    eng.toggle_pause()  # paused: _resume cleared
    assert not eng._resume.is_set()
    eng.stop()
    assert eng._stop.is_set()
    assert eng._resume.is_set()  # released so the consumer can exit
    assert eng._paused is False


def test_stop_before_speak_is_harmless():
    eng = SayEngine(_cfg())
    eng.stop()  # no stream, no threads — must not raise
    assert eng._stop.is_set()


def test_pause_with_mock_stream_stops_and_starts():
    """toggle_pause drives stream.stop()/start() when a stream is live."""

    class FakeStream:
        def __init__(self):
            self.stopped = 0
            self.started = 0

        def stop(self):
            self.stopped += 1

        def start(self):
            self.started += 1

    eng = SayEngine(_cfg())
    fake = FakeStream()
    eng._stream = fake
    eng.toggle_pause()  # pause -> stream.stop()
    assert fake.stopped == 1 and fake.started == 0
    eng.toggle_pause()  # resume -> stream.start()
    assert fake.started == 1


def test_stop_aborts_live_stream_and_cleans_tmp(tmp_path):
    class FakeStream:
        def __init__(self):
            self.aborted = 0

        def abort(self):
            self.aborted += 1

    eng = SayEngine(_cfg())
    fake = FakeStream()
    eng._stream = fake
    leaked = tmp_path / "say-leak.wav"
    leaked.write_bytes(b"junk")
    eng._tmp_paths.add(str(leaked))
    eng.stop()
    assert fake.aborted == 1
    assert not leaked.exists()  # temp cleaned up by stop()
    assert not eng._tmp_paths
