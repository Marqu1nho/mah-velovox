"""Tests for parse.py + script.py — markdown structure and prosody."""

import copy

import pytest

from readaloud.clean import clean
from readaloud.config import DEFAULTS
from readaloud.parse import parse
from readaloud.script import build_script, make_script

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


def test_parse_header():
    blocks = parse("# Big Title\n\nbody text", DEFAULTS)
    assert blocks[0].kind == "header"
    assert blocks[0].text == "Big Title"
    assert blocks[0].level == 1


def test_parse_allcaps_pseudo_header():
    blocks = parse("INSTALLATION STEPS\n\nfirst do this", DEFAULTS)
    assert blocks[0].kind == "header"
    assert blocks[0].text == "INSTALLATION STEPS"


def test_parse_allcaps_disabled():
    cfg = _cfg(**{"headers.treat_all_caps_lines_as_headers": False})
    blocks = parse("INSTALLATION STEPS\n\nfirst do this", cfg)
    assert blocks[0].kind == "paragraph"


def test_parse_fenced_code_block():
    text = "before\n\n```python\ndef f():\n    pass\n```\n\nafter"
    blocks = parse(text, DEFAULTS)
    kinds = [b.kind for b in blocks]
    assert "code" in kinds
    code = next(b for b in blocks if b.kind == "code")
    assert code.lines == ["def f():", "    pass"]


def test_parse_bullet_list():
    text = "- one\n- two\n- three"
    blocks = parse(text, DEFAULTS)
    assert [b.kind for b in blocks] == ["list_item"] * 3
    assert blocks[0].text == "one"


def test_parse_table():
    text = "| Name | Age |\n| --- | --- |\n| Alice | 30 |\n| Bob | 25 |"
    blocks = parse(text, DEFAULTS)
    table = next(b for b in blocks if b.kind == "table")
    assert table.rows[0] == ["Name", "Age"]
    assert ["Alice", "30"] in table.rows


def test_parse_strips_inline_markup():
    blocks = parse("this is **bold** and `code` and a [link](http://x.com)", DEFAULTS)
    para = blocks[0]
    assert "**" not in para.text and "`" not in para.text
    assert "bold" in para.text and "code" in para.text and "link" in para.text


def test_parse_horizontal_rule():
    blocks = parse("above\n\n---\n\nbelow", DEFAULTS)
    assert any(b.kind == "hr" for b in blocks)


def test_script_header_is_slower_with_pauses():
    chunks = make_script("## Section\n\nbody", DEFAULTS)
    header = chunks[0]
    assert header.kind == "header"
    assert header.rate_factor == DEFAULTS["headers"]["rate_factor"]
    assert header.pause_before_ms == DEFAULTS["headers"]["pause_before_ms"]
    assert header.pause_after_ms == DEFAULTS["headers"]["pause_after_ms"]


def test_script_code_block_announced_by_default():
    text = "```python\na = 1\nb = 2\n```"
    chunks = make_script(text, DEFAULTS)
    announce = next(c for c in chunks if c.kind == "code_announce")
    assert "code block" in announce.text
    assert "2 lines" in announce.text


def test_script_code_block_silent_skip():
    cfg = _cfg(**{"code_blocks.mode": "silent-skip"})
    text = "```\na\nb\n```"
    chunks = make_script(text, cfg)
    assert all(c.kind not in ("code", "code_announce") for c in chunks)


def test_script_code_block_read():
    cfg = _cfg(**{"code_blocks.mode": "read"})
    text = "```\nhello world\n```"
    chunks = make_script(text, cfg)
    code = next(c for c in chunks if c.kind == "code")
    assert "hello world" in code.text


def test_script_sentence_splits_paragraph():
    chunks = make_script("First sentence. Second sentence. Third one.", DEFAULTS)
    para = [c for c in chunks if c.kind == "paragraph"]
    assert len(para) == 3


def test_script_list_items_have_pause():
    chunks = make_script("- alpha\n- beta", DEFAULTS)
    items = [c for c in chunks if c.kind == "list_item"]
    assert len(items) == 2
    assert items[0].pause_after_ms == DEFAULTS["pauses"]["list_item_ms"]


def test_script_hr_becomes_pause():
    chunks = make_script("a\n\n---\n\nb", DEFAULTS)
    hr = next(c for c in chunks if c.kind == "hr")
    assert hr.pause_after_ms == DEFAULTS["pauses"]["horizontal_rule_ms"]
    assert hr.text == ""


def test_script_table_read_row_wise():
    text = "| Name | Age |\n| --- | --- |\n| Alice | 30 |"
    chunks = make_script(text, DEFAULTS)
    table = next(c for c in chunks if c.kind == "table")
    assert "Name: Alice" in table.text
    assert "Age: 30" in table.text


def test_end_to_end_tui_paste_pipeline():
    """The full clean->parse->script pipeline on realistic TUI output."""
    cfg = DEFAULTS
    cleaned = clean(TUI_PASTE, cfg)
    chunks = build_script(parse(cleaned, cfg), cfg)
    kinds = [c.kind for c in chunks]
    # Header present and slower.
    header = next(c for c in chunks if c.kind == "header")
    assert "Build summary" in header.text
    assert header.rate_factor < 1.0
    # Code block announced, not recited.
    assert any(c.kind == "code_announce" for c in chunks)
    assert not any("def hello" in c.text for c in chunks)
    # List items cleaned: URL -> domain, path -> basename.
    text_all = " ".join(c.text for c in chunks)
    assert "github.com" in text_all
    assert "clean.py" in text_all
    assert "/Users/marcop" not in text_all
