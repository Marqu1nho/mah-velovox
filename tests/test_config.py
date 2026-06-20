"""Tests for config.py — defaults, deep-merge, validation."""

import pytest

from readaloud.config import ConfigError, DEFAULTS, load_config


def test_explicit_missing_path_is_error(tmp_path):
    # An explicit --config path that does not exist is a user error, not a
    # silent fall-through to defaults.
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_default_path_missing_returns_defaults(monkeypatch, tmp_path):
    # With no explicit path and no file at the default location, pure defaults.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    cfg = load_config()
    assert cfg["engine"] == "say"
    assert cfg["voice"]["base_wpm"] == 240


def test_deep_merge_preserves_unspecified_keys(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("voice:\n  speed: 1.4\n")
    cfg = load_config(p)
    assert cfg["voice"]["speed"] == 1.4
    # Other voice keys retain defaults.
    assert cfg["voice"]["base_wpm"] == 240
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


@pytest.mark.parametrize("key,yaml_snippet", [
    ("code_blocks.mode", "code_blocks:\n  mode: hum\n"),
    ("clean.rejoin", "clean:\n  rejoin: sometimes\n"),
    ("clean.urls", "clean:\n  urls: shorten\n"),
    ("clean.paths", "clean:\n  paths: dirname\n"),
    ("clean.emoji", "clean:\n  emoji: speak\n"),
])
def test_enum_keys_validated(tmp_path, key, yaml_snippet):
    p = tmp_path / "config.yaml"
    p.write_text(yaml_snippet)
    with pytest.raises(ConfigError, match=key.replace(".", r"\.")):
        load_config(p)


def test_negative_wpm_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("voice:\n  base_wpm: -10\n")
    with pytest.raises(ConfigError, match="base_wpm"):
        load_config(p)


def test_bool_wpm_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("voice:\n  base_wpm: true\n")
    with pytest.raises(ConfigError, match="base_wpm"):
        load_config(p)


def test_negative_pause_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("pauses:\n  paragraph_ms: -5\n")
    with pytest.raises(ConfigError, match="paragraph_ms"):
        load_config(p)


def test_zero_pause_allowed(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("pauses:\n  paragraph_ms: 0\n")
    cfg = load_config(p)
    assert cfg["pauses"]["paragraph_ms"] == 0


def test_malformed_yaml_clear_error(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("engine: [unclosed\n")
    with pytest.raises(ConfigError, match="parse"):
        load_config(p)


def test_defaults_match_spec_keys():
    # All top-level config keys must be present in DEFAULTS (see README.md).
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


def test_comma_ms_default_is_150():
    assert DEFAULTS["pauses"]["comma_ms"] == 150


def test_comma_ms_zero_allowed(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("pauses:\n  comma_ms: 0\n")
    cfg = load_config(p)
    assert cfg["pauses"]["comma_ms"] == 0


def test_comma_ms_negative_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("pauses:\n  comma_ms: -1\n")
    with pytest.raises(ConfigError, match="comma_ms"):
        load_config(p)
