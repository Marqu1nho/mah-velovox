"""Lightweight, line-based markdown structure pass.

No full CommonMark. We detect headers, ALL-CAPS pseudo-headers, fenced
code blocks, lists, blockquotes, tables, horizontal rules, and paragraphs,
emitting a flat list of structural Block objects for script.py to turn
into a speech script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADER = re.compile(r"^\s*(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_BLOCKQUOTE = re.compile(r"^\s*>\s?(.*)$")
_HR = re.compile(r"^\s*(?:[-*_])\s*(?:[-*_]\s*){2,}$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]+\|?\s*$")
_ALLCAPS = re.compile(r"^[A-Z0-9][A-Z0-9 \-_/&.,()']*$")


@dataclass
class Block:
    """A structural unit of the document."""

    kind: str  # header | paragraph | list_item | code | blockquote | table | hr
    text: str = ""
    level: int = 0  # header level
    lines: list[str] = field(default_factory=list)  # code/table raw lines
    rows: list[list[str]] = field(default_factory=list)  # table cells


def _looks_like_allcaps_header(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 2 or len(stripped) > 60:
        return False
    if not _ALLCAPS.match(stripped):
        return False
    # Need at least two alpha characters to avoid e.g. "OK" being too trivial,
    # and at least one letter overall.
    letters = [c for c in stripped if c.isalpha()]
    return len(letters) >= 2


def _split_table_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def parse(text: str, cfg: dict[str, Any]) -> list[Block]:
    """Parse cleaned text into a flat list of structural Blocks."""
    headers_cfg = cfg.get("headers", {})
    allcaps_headers = headers_cfg.get("treat_all_caps_lines_as_headers", True)

    lines = text.split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)

    para_buf: list[str] = []

    def flush_para() -> None:
        nonlocal para_buf
        if para_buf:
            joined = " ".join(s.strip() for s in para_buf if s.strip())
            joined = _strip_inline(joined)
            if joined:
                blocks.append(Block(kind="paragraph", text=joined))
            para_buf = []

    while i < n:
        line = lines[i]

        # Fenced code block.
        fence = _FENCE.match(line)
        if fence:
            flush_para()
            marker = fence.group(1)[0]
            body: list[str] = []
            i += 1
            while i < n:
                if re.match(rf"^\s*{re.escape(marker)}{{3,}}\s*$", lines[i]):
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            blocks.append(Block(kind="code", lines=body))
            continue

        # Blank line -> paragraph boundary.
        if not line.strip():
            flush_para()
            i += 1
            continue

        # Horizontal rule.
        if _HR.match(line):
            flush_para()
            blocks.append(Block(kind="hr"))
            i += 1
            continue

        # ATX header.
        h = _HEADER.match(line)
        if h:
            flush_para()
            level = len(h.group(1))
            blocks.append(
                Block(kind="header", text=_strip_inline(h.group(2)), level=level)
            )
            i += 1
            continue

        # Table: a row line followed by (or preceding) more pipe rows.
        if _TABLE_ROW.match(line):
            # Peek: a real table has at least a header + separator or 2 rows.
            table_lines = [line]
            j = i + 1
            while j < n and _TABLE_ROW.match(lines[j]):
                table_lines.append(lines[j])
                j += 1
            if len(table_lines) >= 2:
                flush_para()
                rows = [
                    _split_table_row(tl)
                    for tl in table_lines
                    if not _TABLE_SEP.match(tl)
                ]
                blocks.append(Block(kind="table", rows=rows))
                i = j
                continue
            # else fall through, treat as paragraph text

        # List item (bullet or ordered).
        bullet = _BULLET.match(line)
        ordered = _ORDERED.match(line)
        if bullet or ordered:
            flush_para()
            content = (bullet or ordered).group(2)
            # Gather wrapped continuation lines (indented, non-marker).
            i += 1
            while i < n and lines[i].strip():
                nxt = lines[i]
                if (
                    _BULLET.match(nxt)
                    or _ORDERED.match(nxt)
                    or _HEADER.match(nxt)
                    or _FENCE.match(nxt)
                    or _HR.match(nxt)
                ):
                    break
                content += " " + nxt.strip()
                i += 1
            blocks.append(Block(kind="list_item", text=_strip_inline(content)))
            continue

        # Blockquote.
        bq = _BLOCKQUOTE.match(line)
        if bq:
            flush_para()
            content = bq.group(1)
            i += 1
            while i < n and _BLOCKQUOTE.match(lines[i]):
                content += " " + _BLOCKQUOTE.match(lines[i]).group(1).strip()
                i += 1
            blocks.append(Block(kind="blockquote", text=_strip_inline(content)))
            continue

        # ALL-CAPS pseudo-header (single line, surrounded by blanks).
        if (
            allcaps_headers
            and _looks_like_allcaps_header(line)
            and not para_buf
            and (i + 1 >= n or not lines[i + 1].strip())
        ):
            flush_para()
            blocks.append(
                Block(kind="header", text=_strip_inline(line.strip()), level=2)
            )
            i += 1
            continue

        # Otherwise accumulate into the current paragraph.
        para_buf.append(line)
        i += 1

    flush_para()
    return blocks


_BOLD_ITALIC = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
_STRIKE = re.compile(r"~~(.+?)~~")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _strip_inline(text: str) -> str:
    """Strip inline markdown markers for the ear.

    - Markdown links [text](url) -> text
    - Inline code `x` -> x (read literally)
    - Bold/italic/strikethrough markers removed
    """
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    text = _INLINE_CODE.sub(lambda m: m.group(1), text)
    text = _STRIKE.sub(lambda m: m.group(1), text)
    # Apply emphasis stripping repeatedly to handle nesting.
    prev = None
    while prev != text:
        prev = text
        text = _BOLD_ITALIC.sub(lambda m: m.group(2), text)
    return text.strip()
