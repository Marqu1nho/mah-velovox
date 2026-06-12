"""Tests for config.py — defaults, deep-merge, validation."""

import pytest

from readaloud.config import ConfigError, DEFAULTS, load_config


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg["engine"] == "say"
    assert cfg["voice"]["base_wpm"] == 190


def test_deep_merge_preserves_unspecified_keys(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("voice:\n  speed: 1.4\n")
    cfg = load_config(p)
    assert cfg["voice"]["speed"] == 1.4
    # Other voice keys retain defaults.
    assert cfg["voice"]["base_wpm"] == 190
    assert cfg["voice"]["say_voice"] == "system"


def test_override_engine(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("engine: kokoro\n")
    cfg = load_config(p)
    assert cfg["engine"] == "kokoro"


def test_invalid_engine_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("engine: festival\n")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "engine" in str(exc.value)


def test_invalid_code_block_mode_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("code_blocks:\n  mode: yell\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_invalid_rejoin_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("clean:\n  rejoin: aggressive\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_non_mapping_top_level_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_empty_file_returns_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("")
    cfg = load_config(p)
    assert cfg == DEFAULTS


def test_defaults_match_spec_keys():
    # §04 contract: top-level keys present.
    for key in (
        "engine",
        "hotkeys",
        "voice",
        "headers",
        "pauses",
        "code_blocks",
        "clean",
        "window_read",
        "limits",
    ):
        assert key in DEFAULTS
