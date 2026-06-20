"""Tests for clean.py — ANSI/TUI scrubbing and line re-join."""

import copy

import pytest

from readaloud.clean import clean, strip_ansi, strip_box_drawing
from readaloud.config import DEFAULTS

from .conftest import TUI_PASTE


def _cfg(**overrides):
    c = copy.deepcopy(DEFAULTS)
    for dotted, val in overrides.items():
        node = c
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = val
    return c


def test_strip_ansi_removes_csi_and_osc():
    s = "\x1b[1mbold\x1b[0m and \x1b]0;title\x07normal"
    assert strip_ansi(s) == "bold and normal"


def test_strip_ansi_removes_control_chars_keeps_newline():
    s = "line1\nline2\x00\x07with junk"
    out = strip_ansi(s)
    assert "\n" in out
    assert "\x00" not in out and "\x07" not in out


def test_strip_box_drawing():
    s = "│ text ├──┤ more │"
    out = strip_box_drawing(s)
    assert "│" not in out and "─" not in out and "├" not in out
    assert "text" in out and "more" in out


def test_clean_removes_box_borders_and_spinner():
    out = clean(TUI_PASTE, DEFAULTS)
    assert "│" not in out and "╭" not in out and "╰" not in out
    assert "⠋" not in out
    # No "vertical bar" style junk words; the header survived.
    assert "Build summary" in out


def test_clean_rejoins_hard_wrapped_lines_smart():
    out = clean(TUI_PASTE, _cfg(**{"clean.rejoin": "smart"}))
    # The wrapped sentence should be on one line.
    assert (
        "fairly long line that the terminal has hard-wrapped" in out
        or "continues onto the next visual line" in out
    )
    # Find the line with "fairly long" and confirm it absorbed the wrap.
    line = next(l for l in out.splitlines() if "fairly long line" in l)
    assert "hard-wrapped at the column boundary" in line


def test_clean_rejoin_never_keeps_lines_separate():
    out = clean(TUI_PASTE, _cfg(**{"clean.rejoin": "never"}))
    line = next(l for l in out.splitlines() if "fairly long line" in l)
    assert "hard-wrapped at the column boundary" not in line


def test_clean_rejoin_always_joins_non_sentence_ends():
    # In 'always' mode, mid-paragraph wrapped lines all collapse onto one line
    # (only structural breaks like list markers/headers stop the join).
    text = "alpha beta gamma\ndelta epsilon\nzeta eta\ntheta\n- a list item"
    out = clean(text, _cfg(**{"clean.rejoin": "always"}))
    assert "alpha beta gamma delta epsilon zeta eta theta" in out
    # The list marker is a structural break, so it stays on its own line.
    assert any(l.strip() == "- a list item" for l in out.splitlines())


def test_clean_does_not_join_after_sentence_end():
    text = "This is a complete sentence here.\nNew sentence begins now also."
    out = clean(text, _cfg(**{"clean.rejoin": "always"}))
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 2


def test_clean_url_to_domain():
    text = "see https://www.github.com/foo/bar for details"
    out = clean(text, _cfg(**{"clean.urls": "domain"}))
    assert "link to github.com" in out
    assert "github.com/foo/bar" not in out


def test_clean_url_full_keeps_url():
    text = "see https://github.com/foo for details"
    out = clean(text, _cfg(**{"clean.urls": "full"}))
    assert "https://github.com/foo" in out


def test_clean_url_skip_removes_url():
    text = "see https://github.com/foo for details"
    out = clean(text, _cfg(**{"clean.urls": "skip"}))
    assert "github.com" not in out
    assert "see" in out and "details" in out


def test_clean_path_to_basename():
    text = "open /Users/marcop/projects/readaloud/clean.py now"
    out = clean(text, _cfg(**{"clean.paths": "basename"}))
    assert "clean.py" in out
    assert "/Users/marcop" not in out


def test_clean_path_full_keeps_path():
    text = "open /Users/marcop/clean.py now"
    out = clean(text, _cfg(**{"clean.paths": "full"}))
    assert "/Users/marcop/clean.py" in out


def test_clean_emoji_skip_default():
    text = "great work 🎉 done ✅"
    out = clean(text, _cfg(**{"clean.emoji": "skip"}))
    assert "🎉" not in out and "✅" not in out
    assert "great work" in out and "done" in out


def test_clean_emoji_name():
    text = "deploy the 🚀 now"
    out = clean(text, _cfg(**{"clean.emoji": "name"}))
    assert "🚀" not in out
    # name mode emits the real Unicode name, not a generic token.
    assert "rocket" in out


def test_clean_drops_symbol_only_lines():
    text = "real content here\n────────\nmore content"
    out = clean(text, DEFAULTS)
    assert "real content here" in out
    assert "more content" in out
    # The divider line became empty and was dropped (no stray line).
    assert "────" not in out


def test_clean_strips_prompt_markers():
    text = "❯ run the command\n$ echo hello"
    out = clean(text, DEFAULTS)
    assert "❯" not in out
    assert "run the command" in out


# ---------------------------------------------------------------------------
# replace — spoken substitutions
# ---------------------------------------------------------------------------

def _replace_cfg(replace_map: dict) -> dict:
    """Return a config with the given replace map, everything else at defaults."""
    c = copy.deepcopy(DEFAULTS)
    c["replace"] = replace_map
    return c


def test_replace_single_char_with_surrounding_spaces():
    """A glyph already padded by spaces is correctly substituted."""
    text = "A → B"
    out = clean(text, _replace_cfg({"→": "to"}))
    assert "A to B" in out


def test_replace_single_char_no_surrounding_spaces():
    """A glyph flush against adjacent text (A→B) reads as separate tokens."""
    text = "A→B"
    out = clean(text, _replace_cfg({"→": "to"}))
    assert "AtoB" not in out
    assert "A to B" in out


def test_replace_multi_char_key():
    """A multi-character abbreviation key is replaced correctly."""
    text = "come w/ me"
    out = clean(text, _replace_cfg({"w/": "with"}))
    assert "w/" not in out
    assert "with" in out


def test_replace_longest_key_first():
    """When two keys overlap, the longer key takes precedence."""
    # If "−>" were applied before "−", the "−" in "−>" would be clobbered first,
    # leaving ">foo" instead of "arrow foo".
    text = "x −> y"
    out = clean(text, _replace_cfg({"−>": "arrow", "−": "minus"}))
    assert "arrow" in out
    assert "minus" not in out


def test_replace_newlines_preserved():
    """Substitutions must not collapse or remove newlines."""
    text = "line one → end\nline two → end"
    # Use rejoin:never so clean() does not merge the two lines before we count
    # them — the point of this test is that _apply_replace preserves newlines,
    # not that rejoin leaves them alone.
    cfg = _replace_cfg({"→": "to"})
    cfg["clean"]["rejoin"] = "never"
    out = clean(text, cfg)
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 2
    assert all("to end" in l for l in lines)


def test_replace_empty_map_is_noop():
    """Empty replace map (the default) leaves text unchanged."""
    text = "A → B"
    cfg_default = copy.deepcopy(DEFAULTS)
    out = clean(text, cfg_default)
    # Without a replace rule, the arrow stays (it's not stripped by default).
    assert "→" in out
