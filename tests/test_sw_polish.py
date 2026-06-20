"""Tests for speakwrite/polish.py — polish levels."""

import logging

import pytest

from speakwrite.polish import polish


# ---------------------------------------------------------------------------
# none — identity
# ---------------------------------------------------------------------------


def test_none_identity():
    assert polish("hello world", "none") == "hello world"


def test_none_preserves_whitespace():
    text = "  hello   world  "
    assert polish(text, "none") == text


def test_none_preserves_no_punctuation():
    text = "no punctuation here"
    assert polish(text, "none") == text


# ---------------------------------------------------------------------------
# punctuation — deterministic, no word changes
# ---------------------------------------------------------------------------


def test_punctuation_capitalizes_first():
    assert polish("hello world.", "punctuation") == "Hello world."


def test_punctuation_already_capitalized():
    assert polish("Hello world.", "punctuation") == "Hello world."


def test_punctuation_adds_terminal_period():
    result = polish("hello world", "punctuation")
    assert result.endswith(".")


def test_punctuation_preserves_question_mark():
    result = polish("is anyone there?", "punctuation")
    assert result.endswith("?")
    assert not result.endswith("?.")


def test_punctuation_preserves_exclamation():
    result = polish("wow that is great!", "punctuation")
    assert result.endswith("!")
    assert not result.endswith("!.")


def test_punctuation_no_word_changes():
    """Words must not be altered — only capitalization and punctuation."""
    text = "the quick brown fox jumps"
    result = polish(text, "punctuation")
    # Remove the added period and check words are unchanged.
    words_in = text.lower().split()
    words_out = result.rstrip(".").lower().split()
    assert words_in == words_out


def test_punctuation_collapses_spaces():
    result = polish("hello   world", "punctuation")
    assert "  " not in result
    # Capitalized after space collapse — check case-insensitively.
    assert "hello world" in result.lower()


def test_punctuation_capitalizes_after_period():
    result = polish("first sentence. second sentence", "punctuation")
    assert "Second sentence" in result


def test_punctuation_capitalizes_after_question():
    result = polish("are you there? yes i am", "punctuation")
    assert "Yes i am" in result


def test_punctuation_empty_string():
    assert polish("", "punctuation") == ""


def test_punctuation_whitespace_only():
    result = polish("   ", "punctuation")
    # May be empty or a single period — but no crash.
    assert isinstance(result, str)


def test_punctuation_already_has_terminal_period():
    result = polish("Hello world.", "punctuation")
    assert result.endswith(".")
    assert not result.endswith("..")


# ---------------------------------------------------------------------------
# light / full — fall back to punctuation with warning
# ---------------------------------------------------------------------------


def test_light_falls_back_to_punctuation(caplog):
    with caplog.at_level(logging.WARNING, logger="speakwrite"):
        result = polish("hello world", "light")
    assert result == polish("hello world", "punctuation")
    assert "light" in caplog.text or "punctuation" in caplog.text or "not yet" in caplog.text


def test_full_falls_back_to_punctuation(caplog):
    with caplog.at_level(logging.WARNING, logger="speakwrite"):
        result = polish("hello world", "full")
    assert result == polish("hello world", "punctuation")
    assert len(caplog.records) >= 1


def test_light_warning_mentions_level(caplog):
    with caplog.at_level(logging.WARNING, logger="speakwrite"):
        polish("test", "light")
    assert any("light" in r.message for r in caplog.records)


def test_full_warning_mentions_level(caplog):
    with caplog.at_level(logging.WARNING, logger="speakwrite"):
        polish("test", "full")
    assert any("full" in r.message for r in caplog.records)
