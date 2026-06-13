"""Tests for say engine command construction (no audio playback)."""

import copy
import os
import tempfile
import threading
import time

import pytest

from readaloud.config import DEFAULTS
from readaloud.engines.say_engine import (
    SAY_BIN,
    SayEngine,
    _coalesce,
    _coalesce_slnc,
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


def test_slnc_merges_paragraphs_and_lists_into_one_invocation():
    chunks = [
        Chunk(text="Para one.", kind="paragraph", pause_after_ms=350),
        Chunk(text="Para two.", kind="paragraph", pause_after_ms=350),
        Chunk(text="item one", kind="list_item", pause_after_ms=200),
        Chunk(text="item two", kind="list_item", pause_after_ms=200),
    ]
    out = _coalesce_slnc(chunks)
    assert len(out) == 1
    assert out[0].text == (
        "Para one. [[slnc 350]] Para two. [[slnc 350]] "
        "item one [[slnc 200]] item two"
    )
    assert out[0].pause_after_ms == 200


def test_slnc_splits_on_rate_change_but_absorbs_hr():
    chunks = [
        Chunk(
            text="Header",
            kind="header",
            rate_factor=0.85,
            pause_before_ms=500,
            pause_after_ms=400,
        ),
        Chunk(text="Body.", kind="paragraph", pause_after_ms=350),
        Chunk(text="", kind="hr", pause_after_ms=600),
        Chunk(text="After rule.", kind="paragraph", pause_after_ms=350),
    ]
    out = _coalesce_slnc(chunks)
    assert len(out) == 2
    assert out[0].text == "Header"
    assert out[1].text == "Body. [[slnc 350]] [[slnc 600]] After rule."
    # The header boundary keeps its python-sleep pauses.
    assert out[0].pause_after_ms == 400
    assert chunks[1].text == "Body."  # input not mutated


# ---------------------------------------------------------------------------
# Pause/resume (no audio: rendering to a temp -o file)
# ---------------------------------------------------------------------------


def test_toggle_pause_flips_state():
    if not os.path.exists(SAY_BIN):
        pytest.skip("say binary not present")
    eng = SayEngine(_cfg())
    assert eng._paused is False
    eng.toggle_pause()
    assert eng._paused is True
    eng.toggle_pause()
    assert eng._paused is False


def test_toggle_pause_noop_after_stop():
    if not os.path.exists(SAY_BIN):
        pytest.skip("say binary not present")
    eng = SayEngine(_cfg())
    eng.stop()
    eng.toggle_pause()
    assert eng._paused is False  # stop() locks pause out


def test_stop_after_pause_terminates_child():
    """A SIGSTOPped say child must still be killed by stop() (SIGCONT first)."""
    if not os.path.exists(SAY_BIN):
        pytest.skip("say binary not present")

    fd, out_path = tempfile.mkstemp(suffix=".aiff")
    os.close(fd)
    eng = SayEngine(_cfg(**{"voice.say_voice": "system"}))

    # Render a long utterance to a file (no speakers). Patch the argv builder
    # so the chunk renders to our temp path instead of the audio device.
    long_text = ("readaloud pause stop test. " * 200).strip()
    orig_build = build_chunk_command

    def patched(chunk, cfg, rate_works=True):
        return orig_build(chunk, cfg, rate_works) + ["-o", out_path]

    import readaloud.engines.say_engine as say_mod

    say_mod.build_chunk_command = patched
    try:
        chunk = Chunk(text=long_text, kind="paragraph")
        worker = threading.Thread(target=eng._speak_chunk, args=(chunk, True))
        worker.start()

        # Wait for the child to be live.
        deadline = time.monotonic() + 5.0
        while eng._proc is None and time.monotonic() < deadline:
            time.sleep(0.01)
        proc = eng._proc
        assert proc is not None, "say child never launched"

        eng.toggle_pause()  # SIGSTOP the child
        assert eng._paused is True
        time.sleep(0.1)

        eng.stop()  # must SIGCONT then SIGTERM
        worker.join(timeout=5.0)
        assert not worker.is_alive(), "worker did not finish after stop()"
        assert proc.poll() is not None, "child still running after stop()"
    finally:
        say_mod.build_chunk_command = orig_build
        try:
            os.unlink(out_path)
        except OSError:
            pass
