"""Tests for say engine command construction (no audio playback)."""

import copy

from readaloud.config import DEFAULTS
from readaloud.engines.say_engine import build_chunk_command
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
