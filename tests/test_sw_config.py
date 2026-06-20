"""Tests for speakwrite/config.py — defaults, deep-merge, validation."""

import pytest

from speakwrite.config import ConfigError, DEFAULTS, load_config


# ---------------------------------------------------------------------------
# Basic loading
# ---------------------------------------------------------------------------


def test_explicit_missing_path_is_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_default_path_missing_returns_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    cfg = load_config()
    assert cfg["engine"] == "parakeet"
    assert cfg["hud"]["width_pct"] == 50


def test_deep_merge_preserves_unspecified_keys(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  width_pct: 80\n")
    cfg = load_config(p)
    assert cfg["hud"]["width_pct"] == 80
    # Other hud keys retain defaults.
    assert cfg["hud"]["lines"] == 4
    assert cfg["hud"]["font_size"] == 20


def test_override_engine(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("engine: mock\n")
    cfg = load_config(p)
    assert cfg["engine"] == "mock"


def test_empty_file_returns_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("")
    cfg = load_config(p)
    assert cfg == DEFAULTS


def test_non_mapping_top_level_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_malformed_yaml_clear_error(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("engine: [unclosed\n")
    with pytest.raises(ConfigError, match="parse"):
        load_config(p)


# ---------------------------------------------------------------------------
# DEFAULTS shape
# ---------------------------------------------------------------------------


def test_defaults_have_required_keys():
    for key in ("engine", "hotkeys", "hud", "polish", "inject", "plan_lane"):
        assert key in DEFAULTS


def test_defaults_engine_is_parakeet():
    assert DEFAULTS["engine"] == "parakeet"


def test_defaults_hotkeys_dictate():
    assert DEFAULTS["hotkeys"]["dictate"] == ["ctrl", "alt", "`"]


def test_defaults_hud_complete():
    hud = DEFAULTS["hud"]
    assert hud["show"] is True
    assert hud["position"] == "bottom-center"
    assert hud["width_pct"] == 50
    assert hud["lines"] == 4
    assert hud["font_size"] == 20
    assert hud["opacity"] == 0.92
    assert hud["fade_after_sentences"] == 2
    assert hud["reanchor_pulse_after_s"] == 3
    assert hud["linger_ms"] == 1500


# ---------------------------------------------------------------------------
# Enum validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key,yaml_snippet", [
    ("engine", "engine: festival\n"),
    ("hotkeys.mode", "hotkeys:\n  mode: hold\n"),
    ("polish", "polish: aggressive\n"),
    ("inject.method", "inject:\n  method: keyboard\n"),
])
def test_enum_keys_rejected(tmp_path, key, yaml_snippet):
    p = tmp_path / "config.yaml"
    p.write_text(yaml_snippet)
    with pytest.raises(ConfigError, match=key.replace(".", r"\.")):
        load_config(p)


def test_valid_engine_apple(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("engine: apple\n")
    cfg = load_config(p)
    assert cfg["engine"] == "apple"


def test_valid_polish_none(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("polish: none\n")
    cfg = load_config(p)
    assert cfg["polish"] == "none"


def test_valid_hotkeys_mode_toggle(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hotkeys:\n  mode: toggle\n")
    cfg = load_config(p)
    assert cfg["hotkeys"]["mode"] == "toggle"


# ---------------------------------------------------------------------------
# Numeric / opacity validation
# ---------------------------------------------------------------------------


def test_width_pct_out_of_range_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  width_pct: 0\n")
    with pytest.raises(ConfigError, match="width_pct"):
        load_config(p)


def test_width_pct_over_100_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  width_pct: 101\n")
    with pytest.raises(ConfigError, match="width_pct"):
        load_config(p)


def test_width_pct_100_ok(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  width_pct: 100\n")
    cfg = load_config(p)
    assert cfg["hud"]["width_pct"] == 100


def test_lines_zero_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  lines: 0\n")
    with pytest.raises(ConfigError, match="lines"):
        load_config(p)


def test_font_size_zero_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  font_size: 0\n")
    with pytest.raises(ConfigError, match="font_size"):
        load_config(p)


def test_opacity_out_of_range_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  opacity: 1.1\n")
    with pytest.raises(ConfigError, match="opacity"):
        load_config(p)


def test_opacity_negative_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  opacity: -0.1\n")
    with pytest.raises(ConfigError, match="opacity"):
        load_config(p)


def test_opacity_zero_and_one_ok(tmp_path):
    for val in (0, 1):
        p = tmp_path / f"cfg_{val}.yaml"
        p.write_text(f"hud:\n  opacity: {val}\n")
        cfg = load_config(p)
        assert cfg["hud"]["opacity"] == val


def test_non_negative_knobs_zero_allowed(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  fade_after_sentences: 0\n  reanchor_pulse_after_s: 0\n  linger_ms: 0\n")
    cfg = load_config(p)
    assert cfg["hud"]["fade_after_sentences"] == 0
    assert cfg["hud"]["reanchor_pulse_after_s"] == 0
    assert cfg["hud"]["linger_ms"] == 0


def test_negative_linger_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  linger_ms: -1\n")
    with pytest.raises(ConfigError, match="linger_ms"):
        load_config(p)


# ---------------------------------------------------------------------------
# hud.position validation
# ---------------------------------------------------------------------------


def test_position_string_valid(tmp_path):
    for pos in ("bottom-center", "top-center", "mouse"):
        p = tmp_path / f"pos_{pos}.yaml"
        p.write_text(f"hud:\n  position: {pos}\n")
        cfg = load_config(p)
        assert cfg["hud"]["position"] == pos


def test_position_invalid_string_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  position: left-side\n")
    with pytest.raises(ConfigError, match="position"):
        load_config(p)


def test_position_mapping_valid(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  position:\n    x: 100\n    y: 200\n")
    cfg = load_config(p)
    assert cfg["hud"]["position"] == {"x": 100, "y": 200}


def test_position_mapping_missing_y_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  position:\n    x: 100\n")
    with pytest.raises(ConfigError, match="position"):
        load_config(p)


def test_position_mapping_non_numeric_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  position:\n    x: left\n    y: 200\n")
    with pytest.raises(ConfigError, match="position"):
        load_config(p)
