"""Tests for mute rules — apply_mute() and clean() integration."""

from __future__ import annotations

import copy

import pytest

from readaloud.clean import apply_mute, clean
from readaloud.config import DEFAULTS, ConfigError, load_config


# ---------------------------------------------------------------------------
# apply_mute unit tests
# ---------------------------------------------------------------------------


def test_apply_mute_empty_rules_noop():
    text = "hello world\nfoo bar"
    assert apply_mute(text, []) == text


def test_apply_mute_literal_excise_midline():
    text = "prefix BADGE suffix\nother line"
    out = apply_mute(text, ["BADGE"])
    assert "BADGE" not in out
    assert "prefix  suffix" in out
    assert "other line" in out


def test_apply_mute_regex_excise():
    text = "foo 123 bar\nbaz"
    out = apply_mute(text, ["re:\\d+"])
    assert "123" not in out
    assert "foo" in out and "bar" in out
    assert "baz" in out


def test_apply_mute_drop_line_literal():
    text = "⎿ Backgrounded agent (↓ to manage · ctrl+o to expand)\nReal content here."
    out = apply_mute(text, ["drop-line:↓ to manage · ctrl+o to expand"])
    lines = out.split("\n")
    assert not any("↓ to manage" in l for l in lines)
    assert any("Real content here." in l for l in lines)


def test_apply_mute_drop_line_regex():
    text = "⎿ tool result\nReal content."
    out = apply_mute(text, ["drop-line:re:^\\s*[⎿⏺]"])
    lines = out.split("\n")
    assert not any(l.strip().startswith("⎿") or l.strip().startswith("⏺") for l in lines if l)
    assert any("Real content." in l for l in lines)


def test_apply_mute_per_line_anchoring():
    # ^ and $ anchor to line start/end (not whole-text start/end)
    text = "hello world\nworld goodbye"
    # regex matching start of line 2 but not line 1
    out = apply_mute(text, ["re:^world"])
    assert "hello world" in out  # line 1: "world" is NOT at start
    assert " goodbye" in out  # line 2: "world" excised


def test_apply_mute_invalid_regex_skipped(caplog):
    import logging
    text = "some text"
    # Invalid regex should not crash; the rule is a no-op
    with caplog.at_level(logging.WARNING, logger="readaloud.clean"):
        out = apply_mute(text, ["re:[invalid"])
    assert out == text  # unchanged
    assert "invalid" in caplog.text.lower() or "skipping" in caplog.text.lower()


def test_apply_mute_case_sensitive():
    text = "This has BADGE in it"
    # Lowercase "badge" should NOT match "BADGE"
    out = apply_mute(text, ["badge"])
    assert "BADGE" in out  # unchanged


def test_apply_mute_multiple_rules():
    text = "foo NOISE bar JUNK end"
    out = apply_mute(text, ["NOISE", "JUNK"])
    assert "NOISE" not in out
    assert "JUNK" not in out
    assert "foo" in out and "bar" in out and "end" in out


def test_apply_mute_drop_line_only_matching_line():
    text = "keep this\ndrop-target line\nalso keep"
    out = apply_mute(text, ["drop-line:drop-target"])
    lines = out.split("\n")
    assert any("keep this" in l for l in lines)
    assert any("also keep" in l for l in lines)
    assert not any("drop-target" in l for l in lines)


# ---------------------------------------------------------------------------
# clean() integration tests
# ---------------------------------------------------------------------------


def _cfg_with_mute(**mute_kwargs):
    """Build a config dict with mute settings."""
    c = copy.deepcopy(DEFAULTS)
    c["mute"] = {"global": [], "by_app": {}, **mute_kwargs}
    return c


def test_clean_global_rule_fires_regardless_of_app():
    cfg = _cfg_with_mute(global_=["NOISE"])
    # Oops, kwarg names can't have underscores for dict keys. Build manually:
    cfg = copy.deepcopy(DEFAULTS)
    cfg["mute"] = {"global": ["NOISE"], "by_app": {}}
    text = "some NOISE content\nclean line"
    out = clean(text, cfg, app=None)
    assert "NOISE" not in out
    assert "content" in out

    out2 = clean(text, cfg, app="Code")
    assert "NOISE" not in out2


def test_clean_by_app_rule_fires_only_for_matching_app():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["mute"] = {"global": [], "by_app": {"Code": ["CHROME"]}}
    text = "normal line\nCHROME stuff here\nmore text"

    out_code = clean(text, cfg, app="Code")
    assert "CHROME" not in out_code
    assert "more text" in out_code

    out_arc = clean(text, cfg, app="Arc")
    assert "CHROME" in out_arc  # not muted for Arc

    out_none = clean(text, cfg, app=None)
    assert "CHROME" in out_none  # not muted when no app


def test_clean_muted_line_husk_dropped_by_pipeline():
    """A drop-line rule leaves an empty string; existing pipeline drops it."""
    cfg = copy.deepcopy(DEFAULTS)
    cfg["mute"] = {"global": ["drop-line:TUI CHROME"], "by_app": {}}
    text = "TUI CHROME\nReal content here."
    out = clean(text, cfg)
    # The husked line should be gone entirely (empty string → dropped)
    assert "TUI CHROME" not in out
    assert "Real content here." in out
    # Confirm there's no stray blank line artefact from the dropped line
    # (the pipeline collapses symbol-only / blank lines)


def test_clean_no_mute_cfg_is_noop():
    """If mute is absent from cfg, clean() behaves as before."""
    cfg = copy.deepcopy(DEFAULTS)
    # Ensure mute key is present (it's in DEFAULTS now) but empty
    assert cfg.get("mute") == {"global": [], "by_app": {}}
    text = "hello world"
    out = clean(text, cfg)
    assert "hello world" in out


def test_clean_app_none_uses_only_global():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["mute"] = {"global": ["GLOBAL_NOISE"], "by_app": {"Code": ["CODE_NOISE"]}}
    text = "GLOBAL_NOISE and CODE_NOISE"
    out = clean(text, cfg, app=None)
    assert "GLOBAL_NOISE" not in out
    assert "CODE_NOISE" in out  # only global applies when app=None


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------


def test_mute_global_not_list_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("mute:\n  global: not-a-list\n")
    with pytest.raises(ConfigError, match="mute.global"):
        load_config(p)


def test_mute_global_list_of_nonstring_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("mute:\n  global:\n    - 123\n")
    with pytest.raises(ConfigError, match="mute.global"):
        load_config(p)


def test_mute_by_app_not_dict_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("mute:\n  by_app: notadict\n")
    with pytest.raises(ConfigError, match="mute.by_app"):
        load_config(p)


def test_mute_by_app_entry_not_list_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("mute:\n  by_app:\n    Code: not-a-list\n")
    with pytest.raises(ConfigError, match="mute.by_app.Code"):
        load_config(p)


def test_mute_not_dict_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("mute: this-is-not-a-dict\n")
    with pytest.raises(ConfigError, match="'mute'"):
        load_config(p)


def test_mute_valid_shapes_load_fine(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "mute:\n"
        "  global:\n"
        "    - 'drop-line:↓ to manage'\n"
        "    - 're:\\d+'\n"
        "  by_app:\n"
        "    Code:\n"
        "      - 'drop-line:re:^\\s*[⏺⎿]'\n"
    )
    cfg = load_config(p)
    assert cfg["mute"]["global"] == ["drop-line:↓ to manage", "re:\\d+"]
    assert "Code" in cfg["mute"]["by_app"]
