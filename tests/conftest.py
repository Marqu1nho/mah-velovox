"""Shared fixtures: realistic Claude-Code-TUI-style pasted output."""

import pytest

from readaloud.config import DEFAULTS, load_config


@pytest.fixture
def cfg():
    """Default config (no user file)."""
    import copy

    return copy.deepcopy(DEFAULTS)


# A chunk of text that looks like what you'd copy out of the Claude Code TUI:
# box-drawing borders, a spinner glyph, ANSI color escapes, a hard-wrapped
# paragraph, a ## header, a fenced code block, and a bullet list.
TUI_PASTE = (
    "\x1b[2m╭───────────────────────────────────────────╮\x1b[0m\n"
    "\x1b[2m│\x1b[0m \x1b[1m## Build summary\x1b[0m                          \x1b[2m│\x1b[0m\n"
    "\x1b[2m╰───────────────────────────────────────────╯\x1b[0m\n"
    "\n"
    "\x1b[38;5;245m⠋\x1b[0m Working on the implementation now. This is a "
    "fairly long line that the\n"
    "terminal has hard-wrapped at the column boundary so it continues onto the\n"
    "next visual line even though it is one sentence.\n"
    "\n"
    "Here is a short paragraph. It has two sentences.\n"
    "\n"
    "```python\n"
    "def hello():\n"
    "    print('hi')\n"
    "```\n"
    "\n"
    "- first bullet item\n"
    "- second bullet item with a link https://www.github.com/foo/bar\n"
    "- third references /Users/marcop/projects/readaloud/clean.py here\n"
)
