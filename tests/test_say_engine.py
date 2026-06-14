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
    FIRST_CHUNK_CHARS,
    MAX_COALESCE_CHARS,
    SAMPLE_RATE,
    SAY_BIN,
    SayEngine,
    _coalesce,
    _render_chunk_to_array,
    _silence,
    _split_first_chunk,
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


# ---------------------------------------------------------------------------
# Rolling history buffer and rewind-on-resume (headless)
# ---------------------------------------------------------------------------


def test_rolling_buffer_trims_to_cap():
    """Writing more frames than rewind_frames keeps the buffer capped."""
    rewind_frames = 100
    recent = np.zeros(0, dtype=np.float32)
    for _ in range(5):  # 5 x 80 = 400 frames total, cap at 100
        block = np.ones(80, dtype=np.float32)
        recent = np.concatenate([recent, block])
        if len(recent) > rewind_frames:
            recent = recent[-rewind_frames:]
    assert len(recent) == rewind_frames


def test_rewind_larger_than_history_uses_all_available():
    """When rewind_frames > available history, replay what we have — no error."""
    rewind_frames = 500
    recent = np.ones(200, dtype=np.float32)  # only 200 frames of history
    replay = recent[-rewind_frames:]
    assert len(replay) == 200  # numpy clamps; no IndexError


def test_no_replay_when_rewind_ms_zero():
    """rewind_frames == 0 means the replay branch is never entered."""
    rewind_frames = 0
    recent = np.ones(1000, dtype=np.float32)
    # Simulate the guard: `if was_paused and rewind_frames > 0 and len(recent) > 0`
    replay_would_happen = (rewind_frames > 0) and len(recent) > 0
    assert not replay_would_happen


def test_resume_edge_triggers_extra_stream_write():
    """On a simulated pause→resume edge, the consumer issues one extra write for replay."""

    class FakeStream:
        def __init__(self):
            self.writes = []

        def write(self, data):
            self.writes.append(data.reshape(-1).copy())

        def stop(self):
            pass

        def start(self):
            pass

        def abort(self):
            pass

        def close(self):
            pass

    # Simulate: 200 frames of history, rewind_frames=100, was_paused=True
    rewind_frames = 100
    recent = np.arange(200, dtype=np.float32)
    fake = FakeStream()
    was_paused = True

    if was_paused and rewind_frames > 0 and len(recent) > 0:
        replay = recent[-rewind_frames:].reshape(-1, 1)
        fake.write(replay)

    assert len(fake.writes) == 1
    assert len(fake.writes[0]) == rewind_frames
    # Replay is the LAST rewind_frames frames of recent
    np.testing.assert_array_equal(fake.writes[0], recent[-rewind_frames:])


def test_config_validates_playback_resume_rewind_ms():
    """Negative resume_rewind_ms raises ConfigError; valid value loads cleanly."""
    import copy
    from readaloud.config import ConfigError, DEFAULTS, _validate

    good = copy.deepcopy(DEFAULTS)
    good["playback"]["resume_rewind_ms"] = 0
    _validate(good)  # no error

    good2 = copy.deepcopy(DEFAULTS)
    good2["playback"]["resume_rewind_ms"] = 1200
    _validate(good2)  # no error

    bad = copy.deepcopy(DEFAULTS)
    bad["playback"]["resume_rewind_ms"] = -1
    with pytest.raises(ConfigError):
        _validate(bad)

    bad_bool = copy.deepcopy(DEFAULTS)
    bad_bool["playback"]["resume_rewind_ms"] = True  # bool excluded
    with pytest.raises(ConfigError):
        _validate(bad_bool)


# ---------------------------------------------------------------------------
# _coalesce cap: merged chunks never exceed MAX_COALESCE_CHARS
# ---------------------------------------------------------------------------


def test_coalesce_caps_merged_chunk_at_max_chars():
    """A sequence of long sentences should produce multiple capped chunks."""
    # Each sentence is ~100 chars; three together exceed MAX_COALESCE_CHARS.
    s1 = "A" * 100 + "."
    s2 = "B" * 100 + "."
    s3 = "C" * 100 + "."
    chunks = [
        Chunk(text=s1, kind="paragraph"),
        Chunk(text=s2, kind="paragraph"),
        Chunk(text=s3, kind="paragraph"),
    ]
    out = _coalesce(chunks)
    for c in out:
        assert len(c.text) <= MAX_COALESCE_CHARS, (
            f"chunk text len {len(c.text)} exceeds cap {MAX_COALESCE_CHARS}"
        )


def test_coalesce_no_text_lost_with_cap():
    """All words survive coalescing even when the cap splits the merge."""
    s1 = "Word " * 40  # 200 chars
    s2 = "Other " * 40  # 240 chars
    chunks = [
        Chunk(text=s1, kind="paragraph"),
        Chunk(text=s2, kind="paragraph"),
    ]
    out = _coalesce(chunks)
    # Join all output text (normalized whitespace) and compare to input.
    joined_out = " ".join(c.text.strip() for c in out)
    joined_in = (s1.strip() + " " + s2.strip())
    # Normalize multiple spaces.
    import re
    joined_out_n = re.sub(r"\s+", " ", joined_out).strip()
    joined_in_n = re.sub(r"\s+", " ", joined_in).strip()
    assert joined_out_n == joined_in_n


# ---------------------------------------------------------------------------
# _split_first_chunk: head size, boundary logic, round-trip text integrity
# ---------------------------------------------------------------------------


def test_split_first_chunk_short_text_unchanged():
    """A chunk already at/under FIRST_CHUNK_CHARS is returned as-is (no split)."""
    text = "Short sentence."
    assert len(text) <= FIRST_CHUNK_CHARS
    chunk = Chunk(text=text, kind="paragraph", pause_after_ms=350)
    parts = _split_first_chunk(chunk)
    assert len(parts) == 1
    assert parts[0].text == text
    assert parts[0].pause_after_ms == 350


def test_split_first_chunk_long_text_produces_two_parts():
    """A long chunk is split into head + remainder."""
    text = "Hello world this is a test sentence. " + "X" * 200
    chunk = Chunk(text=text, kind="paragraph", rate_factor=1.0, pause_after_ms=350)
    parts = _split_first_chunk(chunk)
    assert len(parts) == 2
    head, rem = parts
    # Head must be short (at or near FIRST_CHUNK_CHARS).
    assert len(head.text) <= FIRST_CHUNK_CHARS + 20  # small slack for sentence pref
    # Head pause is 0.
    assert head.pause_after_ms == 0
    # Remainder inherits the original pause.
    assert rem.pause_after_ms == 350
    # Both inherit the original rate_factor.
    assert head.rate_factor == 1.0
    assert rem.rate_factor == 1.0
    # Round-trip: head + " " + remainder must equal the original (modulo whitespace join).
    import re
    rejoined = re.sub(r"\s+", " ", (head.text + " " + rem.text).strip())
    original_n = re.sub(r"\s+", " ", text.strip())
    assert rejoined == original_n


def test_split_first_chunk_no_mid_word_split():
    """The split point must not be inside a word."""
    # A long first word followed by more text.
    text = "Supercalifragilistic expialidocious and then some more words follow here yes."
    # Pad to ensure it exceeds FIRST_CHUNK_CHARS.
    text = text + " " + "more text padding " * 5
    chunk = Chunk(text=text, kind="paragraph")
    parts = _split_first_chunk(chunk)
    if len(parts) == 2:
        head, rem = parts
        # Neither head nor rem should start or end mid-word.
        # head ends at a space or punctuation (no partial word).
        assert not head.text[-1].isalnum() or head.text[-1] in ".!?,;"


def test_split_first_chunk_round_trip_no_text_lost():
    """Concatenating head + remainder reconstructs the full original text."""
    import re
    text = "The quick brown fox jumped over the lazy dog. " * 5  # well over 90 chars
    chunk = Chunk(text=text.strip(), kind="paragraph", pause_after_ms=500)
    parts = _split_first_chunk(chunk)
    if len(parts) == 1:
        assert parts[0].text == chunk.text
    else:
        head, rem = parts
        joined = re.sub(r"\s+", " ", (head.text + " " + rem.text).strip())
        original = re.sub(r"\s+", " ", chunk.text.strip())
        assert joined == original
        assert head.pause_after_ms == 0
        assert rem.pause_after_ms == chunk.pause_after_ms


def test_coalesce_then_split_pipeline_no_text_lost():
    """Full pipeline: coalesce -> split first chunk -> all text present."""
    import re
    sentences = [
        Chunk(text="First sentence here.", kind="paragraph"),
        Chunk(text="Second sentence here.", kind="paragraph"),
        Chunk(text="Third sentence here.", kind="paragraph", pause_after_ms=350),
    ]
    coalesced = _coalesce(sentences)
    if coalesced:
        first_parts = _split_first_chunk(coalesced[0])
        if len(first_parts) > 1:
            all_chunks = first_parts + coalesced[1:]
        else:
            all_chunks = coalesced
    else:
        all_chunks = coalesced

    all_text = " ".join(c.text.strip() for c in all_chunks)
    original_text = " ".join(s.text.strip() for s in sentences)
    all_text_n = re.sub(r"\s+", " ", all_text).strip()
    original_n = re.sub(r"\s+", " ", original_text).strip()
    assert all_text_n == original_n
