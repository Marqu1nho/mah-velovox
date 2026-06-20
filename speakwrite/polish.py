"""Text polishing for speakwrite transcripts.

Levels:
  none        — identity, no changes
  punctuation — deterministic, no word changes: capitalize sentence starts,
                collapse whitespace runs, ensure terminal punctuation
  light / full — fall back to punctuation with a warning (LLM polish not yet built)
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger("speakwrite")

# Sentence-ending punctuation characters.
_SENTENCE_END = frozenset(".?!")


def _punctuation_polish(text: str) -> str:
    """Deterministic punctuation polish — no word changes.

    1. Collapse runs of whitespace (but preserve newlines as single newlines).
    2. Capitalize the first letter of each sentence.
    3. Ensure the text ends with . ? or !
    """
    if not text:
        return text

    # Collapse internal whitespace runs (spaces/tabs → single space).
    text = re.sub(r"[ \t]+", " ", text).strip()

    if not text:
        return text

    # Capitalize first character of the whole text.
    chars = list(text)
    if chars[0].isalpha():
        chars[0] = chars[0].upper()

    # Capitalize after sentence-ending punctuation followed by whitespace.
    i = 0
    while i < len(chars) - 1:
        if chars[i] in _SENTENCE_END:
            # Skip any trailing spaces/newlines.
            j = i + 1
            while j < len(chars) and chars[j] in (" ", "\t", "\n"):
                j += 1
            if j < len(chars) and chars[j].isalpha():
                chars[j] = chars[j].upper()
            i = j
        else:
            i += 1

    text = "".join(chars)

    # Ensure terminal punctuation.
    if text and text[-1] not in _SENTENCE_END:
        text += "."

    return text


def polish(text: str, level: str) -> str:
    """Apply the requested polish level to ``text``.

    Args:
        text:  The raw transcript text.
        level: One of "none", "punctuation", "light", "full".

    Returns:
        The polished text string.
    """
    if level == "none":
        return text

    if level == "punctuation":
        return _punctuation_polish(text)

    if level in ("light", "full"):
        _log.warning(
            "polish level %r is not yet implemented; falling back to 'punctuation'",
            level,
        )
        return _punctuation_polish(text)

    # Unknown level — identity with a warning.
    _log.warning("unknown polish level %r; returning text unchanged", level)
    return text
