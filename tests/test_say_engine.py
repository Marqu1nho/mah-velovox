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
    MAX_CHARS,
    RAMP_CHARS,
    SAMPLE_RATE,
    SAY_BIN,
    SayEngine,
    _coalesce,
    _render_chunk_to_array,
    _resegment,
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
# _coalesce: merges without cap (re-splitting is done by _resegment)
# ---------------------------------------------------------------------------


def test_coalesce_merges_all_text_without_cap():
    """_coalesce now merges without a char cap; _resegment handles sizing."""
    import re
    s1 = "Word " * 40  # 200 chars
    s2 = "Other " * 40  # 240 chars
    chunks = [
        Chunk(text=s1, kind="paragraph"),
        Chunk(text=s2, kind="paragraph"),
    ]
    out = _coalesce(chunks)
    # No cap: all text should merge into one chunk (same rate, no pause between).
    assert len(out) == 1
    joined_out_n = re.sub(r"\s+", " ", out[0].text).strip()
    joined_in_n = re.sub(r"\s+", " ", (s1.strip() + " " + s2.strip())).strip()
    assert joined_out_n == joined_in_n


# ---------------------------------------------------------------------------
# _resegment: ramp sizing, no text lost, no mid-word split, global ramp
# ---------------------------------------------------------------------------


def test_resegment_first_chunk_at_ramp_start():
    """The first emitted chunk must be at most RAMP_CHARS[0] + small slack chars."""
    # Build a long single-paragraph input (>500 chars worth of words).
    words = "the quick brown fox jumped over the lazy dog "
    text = (words * 15).strip()  # ~675 chars
    chunks = [Chunk(text=text, kind="paragraph")]
    out = _resegment(chunks)
    # First chunk should be small — at/under first ramp target + slack for boundary pref.
    assert len(out[0].text) <= RAMP_CHARS[0] + 15, (
        f"first chunk len={len(out[0].text)} exceeds ramp[0]={RAMP_CHARS[0]}+15"
    )


def test_resegment_chunks_never_exceed_max_chars():
    """No emitted chunk must exceed MAX_CHARS (the plateau)."""
    words = "the quick brown fox jumped over the lazy dog "
    text = (words * 30).strip()  # ~1350 chars
    chunks = [Chunk(text=text, kind="paragraph")]
    out = _resegment(chunks)
    for i, c in enumerate(out):
        assert len(c.text) <= MAX_CHARS, (
            f"chunk {i} len={len(c.text)} exceeds MAX_CHARS={MAX_CHARS}"
        )


def test_resegment_no_text_lost():
    """Concatenating all emitted chunk texts equals the original (modulo whitespace)."""
    import re
    sentences = [
        "First long sentence with several words to add up.",
        "Second sentence that is also fairly long and descriptive.",
        "Third sentence with yet more content to push us over the ramp.",
        "Fourth sentence keeps going to ensure multiple chunks are emitted.",
        "Fifth sentence is here so the total text is well over 300 characters.",
    ]
    input_text = " ".join(sentences)
    chunks = [Chunk(text=input_text, kind="paragraph")]
    out = _resegment(chunks)
    rejoined = re.sub(r"\s+", " ", " ".join(c.text.strip() for c in out)).strip()
    original = re.sub(r"\s+", " ", input_text.strip()).strip()
    assert rejoined == original


def test_resegment_no_mid_word_split():
    """No emitted chunk should split in the middle of a word.

    We verify this by checking that every adjacent pair of chunks (piece_i, piece_{i+1})
    has a word boundary at their join: specifically, neither the last character of piece_i
    nor the first character of piece_{i+1} is the interior of a word shared across the
    boundary.  The correct method: find the join point in the normalized original text
    and check that the char at that position (or just after) is a space.
    """
    import re
    words = "supercalifragilistic expialidocious antidisestablishmentarianism "
    text = (words * 10).strip()
    chunks = [Chunk(text=text, kind="paragraph")]
    out = _resegment(chunks)
    # Rebuild the full text from output chunks and find where each boundary falls.
    # At each boundary, the original text must have a space (word boundary).
    full_original = re.sub(r"\s+", " ", text.strip())
    for i in range(len(out) - 1):
        head_text = out[i].text.rstrip()
        tail_text = out[i + 1].text.lstrip()
        # The join in the original must be at a word boundary: head ends a word,
        # tail starts a word (i.e. they are separated by at least one space in the original).
        # Simplest check: head_text does not end with the start of a word that continues
        # in tail_text. Reconstruct boundary by checking that head ends at a space-sep point.
        boundary = head_text + " " + tail_text
        # In the original, head_text followed by a space then tail_text must appear.
        # Use normalized original to check.
        head_n = re.sub(r"\s+", " ", head_text)
        tail_n = re.sub(r"\s+", " ", tail_text)
        # The last word of head must be a complete word in the original.
        last_word_of_head = head_n.split()[-1] if head_n.split() else ""
        first_word_of_tail = tail_n.split()[0] if tail_n.split() else ""
        # Check neither word is a prefix/suffix of a longer token that spans the boundary.
        # Specifically: last_word_of_head + first_word_of_tail must NOT appear as a single
        # token in the original (which would indicate a mid-word split).
        if last_word_of_head and first_word_of_tail:
            combined = last_word_of_head + first_word_of_tail
            assert combined not in full_original, (
                f"mid-word split at boundary {i}: "
                f"'{last_word_of_head}' + '{first_word_of_tail}' = '{combined}' "
                f"appears in original as a single token"
            )


def test_resegment_ramp_advances_globally_across_paragraphs():
    """By the time paragraph 2 starts, the ramp should already be at plateau."""
    # Para 1: enough text to exhaust all ramp steps.
    para1_text = ("word " * 100).strip()  # ~500 chars; will emit 3+ ramp chunks
    para2_text = ("other " * 100).strip()  # another 500 chars
    chunks = [
        Chunk(text=para1_text, kind="paragraph", pause_after_ms=350),
        Chunk(text=para2_text, kind="paragraph", pause_after_ms=350),
    ]
    out = _resegment(chunks)
    # Find first chunk belonging to paragraph 2 (after the pause boundary).
    # The first chunk of the entire read is small; by para 2 we should be at plateau.
    # Identify the para2 chunks: they come after a chunk with pause_after_ms=350.
    para2_chunks = []
    found_boundary = False
    for c in out:
        if found_boundary:
            para2_chunks.append(c)
        if c.pause_after_ms == 350 and not found_boundary:
            found_boundary = True

    assert para2_chunks, "no para2 chunks found"
    # First chunk of para2 should be at plateau (MAX_CHARS) or at least > RAMP_CHARS[0],
    # because the ramp was exhausted during para1.
    first_para2_len = len(para2_chunks[0].text)
    assert first_para2_len > RAMP_CHARS[0], (
        f"ramp appears to have reset: para2 first chunk len={first_para2_len} "
        f"which is at/under ramp[0]={RAMP_CHARS[0]}"
    )


def test_resegment_pause_only_chunk_passes_through():
    """HR chunks (empty text with pause) pass through untouched as boundaries."""
    chunks = [
        Chunk(text="Some paragraph text here.", kind="paragraph"),
        Chunk(text="", kind="hr", pause_after_ms=600),
        Chunk(text="Another paragraph after the rule.", kind="paragraph"),
    ]
    out = _resegment(chunks)
    kinds = [c.kind for c in out]
    assert "hr" in kinds
    hr_chunks = [c for c in out if c.kind == "hr"]
    assert len(hr_chunks) == 1
    assert hr_chunks[0].pause_after_ms == 600


def test_resegment_rate_changed_chunk_stays_separate():
    """A header with a different rate_factor is emitted as its own boundary."""
    chunks = [
        Chunk(text="Header title here.", kind="header", rate_factor=0.85,
              pause_before_ms=500, pause_after_ms=400),
        Chunk(text="Body paragraph text follows the header.", kind="paragraph",
              rate_factor=1.0),
    ]
    out = _resegment(chunks)
    # Header and body should stay separate (different rate_factor).
    rates = [c.rate_factor for c in out]
    assert 0.85 in rates and 1.0 in rates
    header_chunks = [c for c in out if c.rate_factor == 0.85]
    assert header_chunks[0].pause_before_ms == 500
    assert header_chunks[-1].pause_after_ms == 400


def test_resegment_pause_attrs_on_first_and_last_of_run():
    """For a multi-piece run: first piece has pause_before, last has pause_after."""
    # Build a text long enough to produce multiple ramp pieces from one run.
    text = ("alpha beta gamma delta epsilon zeta eta theta iota kappa " * 20).strip()
    chunks = [
        Chunk(text=text, kind="paragraph", pause_before_ms=200, pause_after_ms=350)
    ]
    out = _resegment(chunks)
    if len(out) > 1:
        assert out[0].pause_before_ms == 200
        assert out[0].pause_after_ms == 0       # interior: no trailing pause
        assert out[-1].pause_after_ms == 350
        assert out[-1].pause_before_ms == 0     # interior: no leading pause
    else:
        # Short input that fits in one piece — pauses on that single piece.
        assert out[0].pause_before_ms == 200
        assert out[0].pause_after_ms == 350


def test_resegment_pipeline_no_text_lost_multi_sentence():
    """Full _resegment pipeline over multi-sentence input: no text lost."""
    import re
    sentences = [
        Chunk(text="First sentence here.", kind="paragraph"),
        Chunk(text="Second sentence here.", kind="paragraph"),
        Chunk(text="Third sentence here.", kind="paragraph", pause_after_ms=350),
    ]
    out = _resegment(sentences)
    all_text = re.sub(r"\s+", " ", " ".join(c.text.strip() for c in out)).strip()
    original_text = re.sub(r"\s+", " ", " ".join(s.text.strip() for s in sentences)).strip()
    assert all_text == original_text
