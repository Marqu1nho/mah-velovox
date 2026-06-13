"""Terminal & noise scrubbing — the reason this project exists.

Pure functions. Input is raw pasted/copied text (often Claude Code TUI
output with box-drawing borders, spinners, ANSI escapes, hard-wrapped
lines). Output is clean prose ready for markdown parsing.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

log = logging.getLogger("readaloud.clean")

# --- ANSI / VT escape sequences -------------------------------------------

# CSI sequences: ESC [ ... final-byte. Also handles the private-mode '?' forms.
_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# OSC sequences: ESC ] ... terminated by BEL or ST (ESC \).
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# Charset designation / single two-char escapes (e.g. ESC(B, ESC=, ESC>).
_ANSI_TWO = re.compile(r"\x1b[()#][0-9A-Za-z]")
_ANSI_SIMPLE = re.compile(r"\x1b[=>NOM78c]")
# Any other lone escape char.
_ANSI_LONE = re.compile(r"\x1b")
# Other C0 control chars except tab/newline (we normalize tab to space).
_C0_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# --- Box-drawing / block / decoration -------------------------------------

# U+2500–U+257F box drawing, U+2580–U+259F block elements,
# U+2800–U+28FF braille (spinners), plus assorted decoration glyphs.
_BOX_BLOCK_BRAILLE = re.compile(r"[─-▟⠀-⣿]")
# Spinner / status decoration glyphs commonly seen in TUIs.
_DECORATION = re.compile(
    r"[•‣●○◐◑◒◓▪▫"
    r"✓✗✔✘✨✻✽❖⁙"
    r"·∙∘◦]"
)

# Emoji-ish ranges (best-effort; we don't need to be exhaustive).
_EMOJI = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f000-\U0001f0ff"
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U0001f1e6-\U0001f1ff"  # regional indicators
    "]+",
    flags=re.UNICODE,
)

# Prompt markers at the start of a line. `>` is deliberately NOT stripped: it
# is a markdown blockquote marker far more often than a shell prompt, and
# parse.py handles blockquotes. The spec only asks to strip `>` "when clearly a
# prompt"; a bare `>` is not clearly a prompt, so we leave it for the parser.
_PROMPT_MARKER = re.compile(r"^[ \t]*(?:❯|%|\$)\s+")

# URL detection.
_URL = re.compile(r"https?://[^\s<>()\[\]]+", re.IGNORECASE)
# Unix-ish absolute or ./ relative file paths with a slash and basename.
_PATH = re.compile(r"(?<![\w/:])(?:~|\.{1,2})?/[^\s:;,'\"()\[\]]+")

_MULTISPACE = re.compile(r"[ \t]+")
_SENTENCE_END = re.compile(r"[.!?:][\"')\]]?$")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_HEADER_LINE = re.compile(r"^\s*#{1,6}\s+")
_BLOCKQUOTE = re.compile(r"^\s*>\s+")
_HR = re.compile(r"^\s*(?:[-*_])\s*(?:[-*_]\s*){2,}$")
_FENCE_LINE = re.compile(r"^\s*(?:`{3,}|~{3,})")


def strip_ansi(text: str) -> str:
    """Remove ANSI/VT escape sequences and stray control characters."""
    text = _ANSI_OSC.sub("", text)
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_TWO.sub("", text)
    text = _ANSI_SIMPLE.sub("", text)
    text = _ANSI_LONE.sub("", text)
    text = text.replace("\t", " ")
    text = _C0_CTRL.sub("", text)
    return text


def strip_box_drawing(text: str) -> str:
    """Replace box-drawing/block/braille glyphs with spaces."""
    return _BOX_BLOCK_BRAILLE.sub(" ", text)


def _domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url, re.IGNORECASE)
    host = m.group(1) if m else url
    if host.lower().startswith("www."):
        host = host[4:]
    return host


def _apply_urls(text: str, mode: str) -> str:
    if mode == "full":
        return text

    def repl(m: re.Match[str]) -> str:
        if mode == "skip":
            return ""
        return f"link to {_domain_of(m.group(0))}"

    return _URL.sub(repl, text)


def _apply_paths(text: str, mode: str) -> str:
    if mode == "full":
        return text

    def repl(m: re.Match[str]) -> str:
        raw = m.group(0).rstrip(".,;:")
        if mode == "skip":
            return ""
        base = raw.rstrip("/").rsplit("/", 1)[-1]
        return base or raw

    return _PATH.sub(repl, text)


def _apply_emoji(text: str, mode: str) -> str:
    if mode != "name":
        return _EMOJI.sub("", text)

    def repl(m: re.Match[str]) -> str:
        # Replace each emoji run with the Unicode names of its characters
        # (e.g. 🚀 -> "rocket"), skipping joiners/variation selectors that
        # carry no spoken meaning.
        names: list[str] = []
        for ch in m.group(0):
            name = unicodedata.name(ch, "").lower()
            if name and "variation selector" not in name and "zero width" not in name:
                names.append(name)
        return f" {' '.join(names)} " if names else " "

    return _EMOJI.sub(repl, text)


def _strip_line_noise(line: str) -> str:
    """Remove box drawing, decoration, prompt markers from a single line."""
    line = strip_box_drawing(line)
    line = _DECORATION.sub("", line)
    line = _PROMPT_MARKER.sub("", line)
    return line


def _is_symbol_only(line: str) -> bool:
    """True if the line has no alphanumeric content left."""
    return not any(ch.isalnum() for ch in line)


def _modal_full_length(lengths: list[int]) -> int:
    """Estimate the terminal column width as the modal long-line length.

    Returns the most common length among the longer lines, used as the
    'full-ish' threshold reference for the rejoin heuristic.
    """
    if not lengths:
        return 0
    return max(lengths)


def _should_join(cur: str, nxt: str, full_len: int) -> bool:
    """Smart rejoin heuristic for a hard-wrapped terminal line.

    Join ``cur`` with ``nxt`` when ``cur`` looks like it was wrapped mid
    paragraph: it is 'full-ish' (>= 0.85 * modal width) and the next line
    continues the sentence (starts lowercase / mid-sentence, not a list
    marker / header / blank).
    """
    cur_stripped = cur.rstrip()
    nxt_stripped = nxt.strip()
    if not cur_stripped or not nxt_stripped:
        return False
    if _SENTENCE_END.search(cur_stripped):
        return False
    if _LIST_MARKER.match(nxt) or _HEADER_LINE.match(nxt) or _HR.match(nxt):
        return False
    if _BLOCKQUOTE.match(nxt) and not _BLOCKQUOTE.match(cur):
        return False
    if full_len and len(cur_stripped) < 0.85 * full_len:
        return False
    first = nxt_stripped[0]
    # Continuation: lowercase letter, or a connective punctuation.
    if first.islower() or first in ",;)":
        return True
    return False


def _rejoin_block(lines: list[str], mode: str) -> list[str]:
    """Re-join hard-wrapped lines within a list of non-blank lines."""
    if mode == "never" or not lines:
        return lines

    full_len = _modal_full_length([len(ln.rstrip()) for ln in lines])
    out: list[str] = []
    buf = lines[0]
    for nxt in lines[1:]:
        if mode == "always":
            join = (
                bool(buf.strip())
                and not _SENTENCE_END.search(buf.rstrip())
                and not _LIST_MARKER.match(nxt)
                and not _HEADER_LINE.match(nxt)
                and not _HR.match(nxt)
            )
        else:  # smart
            join = _should_join(buf, nxt, full_len)
        if join:
            buf = buf.rstrip() + " " + nxt.strip()
        else:
            out.append(buf)
            buf = nxt
    out.append(buf)
    return out


def _parse_mute_rules(rules: list[str]) -> list[tuple]:
    """Parse raw rule strings into (drop_line: bool, kind: str, payload) tuples.

    kind is 'literal' or a compiled regex object.
    Invalid regexes are logged and skipped.
    """
    parsed = []
    for rule in rules:
        drop_line = False
        s = rule
        if s.startswith("drop-line:"):
            drop_line = True
            s = s[len("drop-line:"):]
        if s.startswith("re:"):
            pattern = s[3:]
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                log.warning("mute rule %r has invalid regex %r: %s; skipping", rule, pattern, exc)
                continue
            parsed.append((drop_line, compiled))
        else:
            parsed.append((drop_line, s))
    return parsed


def _parse_block_rules(rules: list[str]) -> list:
    """Parse block start-pattern strings into matchers.

    A block rule has no ``drop-line:`` prefix (blocks are inherently drop).
    Each matcher is either a literal string or a compiled regex object,
    following the same ``re:`` grammar as per-line mute rules. Invalid
    regexes are logged and skipped.
    """
    parsed: list = []
    for rule in rules:
        if rule.startswith("re:"):
            pattern = rule[3:]
            try:
                parsed.append(re.compile(pattern))
            except re.error as exc:
                log.warning(
                    "mute block rule %r has invalid regex %r: %s; skipping",
                    rule,
                    pattern,
                    exc,
                )
                continue
        else:
            parsed.append(rule)
    return parsed


def _block_matches(line: str, matchers: list) -> bool:
    for m in matchers:
        if isinstance(m, str):
            if m in line:
                return True
        elif m.search(line):
            return True
    return False


def apply_block_mute(text: str, block_rules: list[str]) -> str:
    """Drop whole multi-line groups whose first line matches a block rule.

    Scanning top to bottom, when a line matches any block start-pattern,
    that line and every following line are dropped UNTIL (but not including)
    the next blank/whitespace-only line. The blank line is preserved as a
    separator, then normal scanning resumes. This catches marker-less
    continuation/body lines (e.g. a Claude Code tool call header plus its
    wrapped command and ``⎿`` result body) that per-line rules can't match.
    """
    if not block_rules:
        return text
    matchers = _parse_block_rules(block_rules)
    if not matchers:
        return text

    lines = text.split("\n")
    out_lines: list[str] = []
    dropping = False
    for line in lines:
        if dropping:
            if line.strip() == "":
                # Blank line ends the group; preserve it and resume scanning.
                dropping = False
                out_lines.append(line)
            # else: still inside the group — drop the line.
            continue
        if _block_matches(line, matchers):
            dropping = True
            continue  # drop the matched start line too
        out_lines.append(line)
    return "\n".join(out_lines)


def apply_mute(text: str, rules: list[str]) -> str:
    """Apply mute rules to text, per line.

    Each rule is one of:
      - plain string  -> excise wherever it appears in a line
      - re:<pattern>  -> excise regex match from the line
      - drop-line:<s> -> if the line contains <s>, replace line with ""
      - drop-line:re:<pattern> -> if regex matches line, replace line with ""
    """
    if not rules:
        return text
    parsed = _parse_mute_rules(rules)
    if not parsed:
        return text

    lines = text.split("\n")
    out_lines = []
    for line in lines:
        for drop_line, payload in parsed:
            if isinstance(payload, str):
                # literal
                if drop_line:
                    if payload in line:
                        line = ""
                        break
                else:
                    line = line.replace(payload, "")
            else:
                # compiled regex
                if drop_line:
                    if payload.search(line):
                        line = ""
                        break
                else:
                    line = payload.sub("", line)
        out_lines.append(line)
    return "\n".join(out_lines)


def clean(text: str, cfg: dict[str, Any], app: str | None = None) -> str:
    """Full cleaning pipeline. Returns cleaned text with blank-line block
    structure preserved (so the parser can still see paragraph boundaries).
    """
    clean_cfg = cfg.get("clean", {})
    rejoin = clean_cfg.get("rejoin", "smart")
    urls_mode = clean_cfg.get("urls", "domain")
    paths_mode = clean_cfg.get("paths", "basename")
    emoji_mode = clean_cfg.get("emoji", "skip")

    text = strip_ansi(text)
    # Normalize CRLF / lone CR to LF so the line-based passes below never see
    # stray carriage returns mid-pipeline (strip_ansi's C0 class skips \x0d).
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Apply mute rules before any other line-based processing.
    mute_cfg = cfg.get("mute", {})

    # Block-mute first: drop whole multi-line groups (start match -> next
    # blank line) so marker-less continuation/body lines vanish before the
    # per-line rules run on the survivors.
    block_rules: list[str] = list(mute_cfg.get("blocks", []))
    if block_rules:
        text = apply_block_mute(text, block_rules)

    mute_rules: list[str] = list(mute_cfg.get("global", []))
    if app:
        mute_rules.extend(mute_cfg.get("by_app", {}).get(app, []))
    if mute_rules:
        text = apply_mute(text, mute_rules)

    raw_lines = text.split("\n")

    # First pass: scrub prose lines, but pass fenced code-block content through
    # verbatim (only ANSI was stripped above). Each emitted element is either a
    # scrubbed prose line, a blank line (paragraph separator), or a special
    # marker tuple ("code", lines) preserved as an opaque block.
    in_fence = False
    fence_char = ""
    prose: list[str] = []  # mixed: str prose/blank lines and "\x00CODE\x00..." markers
    code_buf: list[str] = []
    code_blocks: list[list[str]] = []

    for line in raw_lines:
        if in_fence:
            code_buf.append(line)
            if _FENCE_LINE.match(line) and line.strip().startswith(fence_char):
                in_fence = False
                code_blocks.append(code_buf)
                prose.append(f"\x00CODE\x00{len(code_blocks) - 1}")
                code_buf = []
            continue

        fm = _FENCE_LINE.match(line)
        if fm:
            in_fence = True
            fence_char = line.strip()[0]
            code_buf = [line]
            continue

        line = _strip_line_noise(line)
        line = _apply_urls(line, urls_mode)
        line = _apply_paths(line, paths_mode)
        line = _apply_emoji(line, emoji_mode)
        line = _MULTISPACE.sub(" ", line).rstrip()
        # Drop lines that are now empty/symbol-only (keep true blanks as
        # paragraph separators, and keep HRs which are structurally meaningful).
        if line.strip() and _is_symbol_only(line) and not _HR.match(line):
            line = ""
        prose.append(line)

    # Unterminated fence: flush as a code block anyway.
    if code_buf:
        code_blocks.append(code_buf)
        prose.append(f"\x00CODE\x00{len(code_blocks) - 1}")

    # Group into blocks separated by blank lines / code markers, rejoin within
    # each prose block, then re-emit with single blank-line separators.
    out_blocks: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        if cur:
            out_blocks.append("\n".join(_rejoin_block(cur, rejoin)))
            cur.clear()

    for line in prose:
        if line.startswith("\x00CODE\x00"):
            flush()
            idx = int(line[len("\x00CODE\x00") :])
            out_blocks.append("\n".join(code_blocks[idx]))
        elif line.strip():
            cur.append(line)
        else:
            flush()
    flush()

    return "\n\n".join(b for b in out_blocks if b.strip()).strip()
