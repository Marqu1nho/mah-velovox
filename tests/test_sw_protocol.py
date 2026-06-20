"""Tests for speakwrite/protocol.py — encode, parse, LineBuffer."""

import json

import pytest

from speakwrite.protocol import (
    LineBuffer,
    encode_done,
    encode_final,
    encode_partial,
    parse_line,
)


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------


def test_encode_partial_volatile():
    line = encode_partial("hello world", True)
    assert line.endswith("\n")
    obj = json.loads(line)
    assert obj == {"text": "hello world", "volatile": True}


def test_encode_partial_committed():
    line = encode_partial("the quick brown fox.", False)
    obj = json.loads(line)
    assert obj == {"text": "the quick brown fox.", "volatile": False}


def test_encode_final():
    line = encode_final("The quick brown fox.")
    assert line.endswith("\n")
    obj = json.loads(line)
    assert obj == {"event": "final", "text": "The quick brown fox."}


def test_encode_done():
    line = encode_done()
    assert line.endswith("\n")
    obj = json.loads(line)
    assert obj == {"event": "done"}


# ---------------------------------------------------------------------------
# parse_line
# ---------------------------------------------------------------------------


def test_parse_line_valid_partial():
    line = '{"text": "hi", "volatile": false}\n'
    obj = parse_line(line)
    assert obj == {"text": "hi", "volatile": False}


def test_parse_line_valid_final():
    line = '{"event": "final", "text": "done"}\n'
    obj = parse_line(line)
    assert obj == {"event": "final", "text": "done"}


def test_parse_line_blank_returns_none():
    assert parse_line("") is None
    assert parse_line("   ") is None
    assert parse_line("\n") is None


def test_parse_line_malformed_returns_none():
    assert parse_line("{bad json") is None
    assert parse_line("not json at all") is None


def test_parse_line_array_returns_none():
    # Top-level arrays are not valid wire objects.
    assert parse_line("[1, 2, 3]") is None


def test_parse_line_never_raises():
    # Should never raise, even on garbage.
    for garbage in ("", "\x00", "}{", "null", "true", "123"):
        result = parse_line(garbage)
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# LineBuffer
# ---------------------------------------------------------------------------


def test_linebuffer_single_complete_line():
    buf = LineBuffer()
    line = '{"text": "hi", "volatile": true}\n'
    results = buf.feed(line)
    assert len(results) == 1
    assert results[0] == {"text": "hi", "volatile": True}


def test_linebuffer_split_across_feeds():
    buf = LineBuffer()
    half = '{"text": "hi"'
    results1 = buf.feed(half)
    assert results1 == []  # no complete line yet
    results2 = buf.feed(', "volatile": false}\n')
    assert len(results2) == 1
    assert results2[0]["text"] == "hi"
    assert results2[0]["volatile"] is False


def test_linebuffer_multiple_lines_in_one_chunk():
    buf = LineBuffer()
    chunk = (
        '{"text": "a", "volatile": true}\n'
        '{"text": "b", "volatile": false}\n'
        '{"event": "done"}\n'
    )
    results = buf.feed(chunk)
    assert len(results) == 3
    assert results[0]["text"] == "a"
    assert results[1]["text"] == "b"
    assert results[2] == {"event": "done"}


def test_linebuffer_partial_remainder_retained():
    buf = LineBuffer()
    buf.feed('{"event": "done"}\n{"partial"')
    # Feed the rest.
    results = buf.feed(': "remainder", "volatile": true}\n')
    assert len(results) == 1
    assert results[0]["partial"] == "remainder"


def test_linebuffer_blank_lines_skipped():
    buf = LineBuffer()
    results = buf.feed("\n\n\n")
    assert results == []


def test_linebuffer_split_at_boundary():
    """Split right at the newline character."""
    buf = LineBuffer()
    line = '{"event": "done"}'
    results1 = buf.feed(line)
    assert results1 == []
    results2 = buf.feed("\n")
    assert results2 == [{"event": "done"}]


def test_linebuffer_round_trip_encode_decode():
    """encode_partial / encode_final / encode_done round-trip through LineBuffer."""
    buf = LineBuffer()
    chunk = (
        encode_partial("the quick", True)
        + encode_partial("the quick brown fox.", False)
        + encode_final("The quick brown fox.")
        + encode_done()
    )
    results = buf.feed(chunk)
    assert len(results) == 4
    assert results[0] == {"text": "the quick", "volatile": True}
    assert results[1] == {"text": "the quick brown fox.", "volatile": False}
    assert results[2] == {"event": "final", "text": "The quick brown fox."}
    assert results[3] == {"event": "done"}
