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


def test_parse_blockquote_merged():
    blocks = parse("> a quoted line\n> that continues", DEFAULTS)
    quotes = [b for b in blocks if b.kind == "blockquote"]
    assert len(quotes) == 1
    assert quotes[0].text == "a quoted line that continues"


def test_parse_preserves_snake_case_identifiers():
    blocks = parse("call my_var_name in the loop", DEFAULTS)
    assert blocks[0].text == "call my_var_name in the loop"


def test_parse_strips_bold_and_italic_keeps_lone_asterisk():
    blocks = parse("this **bold** and *ital* but a * b stays", DEFAULTS)
    text = blocks[0].text
    assert "bold" in text and "ital" in text
    assert "**" not in text
    assert "a * b" in text


def test_parse_image_keeps_alt_text():
    blocks = parse("see ![the diagram](x.png) above", DEFAULTS)
    assert "the diagram" in blocks[0].text
    assert "x.png" not in blocks[0].text


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


def test_script_blockquote_becomes_spoken_chunk():
    chunks = make_script("> a wise quote here", DEFAULTS)
    quote = next(c for c in chunks if c.kind == "blockquote")
    assert "a wise quote here" in quote.text


def test_split_sentences_basic():
    from readaloud.script import split_sentences

    assert split_sentences("One here. Two there. Three!") == [
        "One here.", "Two there.", "Three!"]


def test_split_sentences_protects_abbreviations():
    from readaloud.script import split_sentences

    out = split_sentences("Use markdown, e.g. headers. Then continue.")
    assert out == ["Use markdown, e.g. headers.", "Then continue."]


def test_split_sentences_protects_decimals():
    from readaloud.script import split_sentences

    out = split_sentences("Version 2.5 shipped today. It works.")
    assert out == ["Version 2.5 shipped today.", "It works."]


def test_split_sentences_hard_caps_very_long_sentences():
    from readaloud.script import split_sentences

    long = "word " * 200  # 1000 chars, no sentence punctuation
    out = split_sentences(long.strip())
    assert len(out) > 1
    assert all(len(p) <= 501 for p in out)


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


# ---------------------------------------------------------------------------
# Clause splitting (comma_ms)
# ---------------------------------------------------------------------------

def test_comma_ms_splits_sentence_into_clauses():
    """comma_ms > 0: a sentence with commas becomes multiple clause chunks."""
    cfg = _cfg(**{"pauses.comma_ms": 150})
    chunks = make_script("The engine renders, plays fast, then ramps up.", cfg)
    para = [c for c in chunks if c.kind == "paragraph"]
    assert len(para) == 3
    # Intermediate clauses get comma_ms; last gets the paragraph pause.
    assert para[0].pause_after_ms == 150
    assert para[1].pause_after_ms == 150
    assert para[2].pause_after_ms == DEFAULTS["pauses"]["paragraph_ms"]


def test_comma_ms_punctuation_stays_attached():
    """The comma/semicolon/colon must stay attached to the preceding clause."""
    cfg = _cfg(**{"pauses.comma_ms": 150})
    chunks = make_script("First clause, second clause.", cfg)
    para = [c for c in chunks if c.kind == "paragraph"]
    assert para[0].text.endswith(",")
    assert "second clause" in para[1].text


def test_comma_ms_no_split_on_number_comma():
    """'3,000' must NOT be split (no space after the comma)."""
    cfg = _cfg(**{"pauses.comma_ms": 150})
    chunks = make_script("There are 3,000 items in the list.", cfg)
    para = [c for c in chunks if c.kind == "paragraph"]
    assert len(para) == 1
    assert "3,000" in para[0].text


def test_comma_ms_no_split_on_time_colon():
    """'10:30' must NOT be split (no space after the colon)."""
    cfg = _cfg(**{"pauses.comma_ms": 150})
    chunks = make_script("The meeting is at 10:30 today.", cfg)
    para = [c for c in chunks if c.kind == "paragraph"]
    assert len(para) == 1


def test_comma_ms_zero_preserves_original_behavior():
    """comma_ms == 0: exactly one chunk per sentence, no clause splitting."""
    cfg = _cfg(**{"pauses.comma_ms": 0})
    chunks = make_script("First sentence, with commas, everywhere.", cfg)
    para = [c for c in chunks if c.kind == "paragraph"]
    assert len(para) == 1
    assert "First sentence, with commas, everywhere." in para[0].text


def test_comma_ms_round_trip_text_preserved():
    """Concatenating clause chunks (with spaces) must equal the original sentence."""
    cfg = _cfg(**{"pauses.comma_ms": 150})
    sentence = "The engine renders, plays fast, then ramps up."
    chunks = make_script(sentence, cfg)
    para = [c for c in chunks if c.kind == "paragraph"]
    reconstructed = " ".join(c.text for c in para)
    assert reconstructed == sentence


def test_comma_ms_applies_to_list_item():
    """Clause splitting applies to list_item blocks."""
    cfg = _cfg(**{"pauses.comma_ms": 100})
    chunks = make_script("- alpha, beta, gamma", cfg)
    items = [c for c in chunks if c.kind == "list_item"]
    assert len(items) == 3
    assert items[0].pause_after_ms == 100
    assert items[1].pause_after_ms == 100
    assert items[2].pause_after_ms == DEFAULTS["pauses"]["list_item_ms"]


def test_comma_ms_applies_to_blockquote():
    """Clause splitting applies to blockquote blocks."""
    cfg = _cfg(**{"pauses.comma_ms": 100})
    chunks = make_script("> a wise quote, indeed", cfg)
    bq = [c for c in chunks if c.kind == "blockquote"]
    assert len(bq) == 2
    assert bq[0].pause_after_ms == 100
    assert bq[1].pause_after_ms == DEFAULTS["pauses"]["paragraph_ms"]


def test_comma_ms_does_not_split_headers():
    """Headers are never clause-split regardless of comma_ms."""
    cfg = _cfg(**{"pauses.comma_ms": 150})
    chunks = make_script("## Big Title, Subtitle", cfg)
    headers = [c for c in chunks if c.kind == "header"]
    assert len(headers) == 1
    assert "Big Title, Subtitle" in headers[0].text


def test_comma_ms_semicolon_and_colon_also_split():
    """Semicolons and colons followed by a space also trigger clause splitting."""
    cfg = _cfg(**{"pauses.comma_ms": 120})
    chunks = make_script("First part; second part: third part.", cfg)
    para = [c for c in chunks if c.kind == "paragraph"]
    assert len(para) == 3
    assert para[0].text.endswith(";")
    assert para[1].text.endswith(":")
