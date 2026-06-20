"""Build a 'speech script' from parsed Blocks.

A speech script is a flat list of Chunk objects:
    {text, kind, rate_factor, pause_before_ms, pause_after_ms}

Long paragraphs are sentence-split so that stop feels instant and kokoro
synthesis can be pipelined.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .parse import Block, parse


@dataclass
class Chunk:
    text: str
    kind: str
    rate_factor: float = 1.0
    pause_before_ms: int = 0
    pause_after_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Sentence splitting: candidate boundaries are . ! ? (plus optional closing
# quotes/parens) followed by whitespace and an uppercase/quote/digit start.
# Candidates preceded by a known abbreviation are rejected, decimals are
# protected (the digit-start lookahead never fires mid-number), and very long
# sentences are sub-split at commas/semicolons so stop always feels instant.
_SENTENCE_BOUNDARY_RE = re.compile(
    r"[.!?][\"')\]”’]*\s+(?=[\"'(\[“‘]?[A-Z0-9])"
)
_ABBREVIATIONS = frozenset(
    a.lower() for a in
    ("e.g", "i.e", "etc", "vs", "cf", "mr", "mrs", "ms", "dr",
     "prof", "st", "no", "fig", "approx", "ca", "al")
)

_MAX_CHUNK_CHARS = 500  # hard cap so stop always feels instant


def _ends_with_abbreviation(text: str, dot_index: int) -> bool:
    """True if the '.' at dot_index terminates a known abbreviation."""
    word_start = dot_index
    while word_start > 0 and (text[word_start - 1].isalnum()
                              or text[word_start - 1] == "."):
        word_start -= 1
    word = text[word_start:dot_index].lower().lstrip(".")
    return word in _ABBREVIATIONS


def split_sentences(text: str) -> list[str]:
    """Split a paragraph into sentence-sized pieces; very long sentences are
    further split at commas/semicolons so stop feels instant and kokoro can
    pipeline synthesis."""
    text = text.strip()
    if not text:
        return []
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        punct = match.group(0)[0]
        if punct == "." and _ends_with_abbreviation(text, match.start()):
            continue
        parts.append(text[start:match.end()].strip())
        start = match.end()
    if start < len(text):
        parts.append(text[start:].strip())
    parts = [p for p in parts if p]

    out: list[str] = []
    for part in parts:
        while len(part) > _MAX_CHUNK_CHARS:
            cut = max(part.rfind(", ", 0, _MAX_CHUNK_CHARS),
                      part.rfind("; ", 0, _MAX_CHUNK_CHARS),
                      part.rfind(" ", 0, _MAX_CHUNK_CHARS))
            if cut <= 0:
                cut = _MAX_CHUNK_CHARS
            out.append(part[:cut + 1].strip().rstrip(",;"))
            part = part[cut + 1:].strip()
        if part:
            out.append(part)
    return out


# Backwards-compatible alias for internal callers.
_split_sentences = split_sentences


# Clause splitting: split at , ; : when followed by whitespace only.
# Numbers like "3,000" or "10:30" are safe because they have no whitespace
# after the punctuation mark.
_CLAUSE_BOUNDARY_RE = re.compile(r"([,;:])(\s+)")


def _split_clauses(sentence: str, comma_ms: int) -> list[tuple[str, int]]:
    """Split a sentence into (clause_text, pause_ms) pairs at , ; :
    followed by whitespace.  The punctuation mark stays attached to the
    preceding clause text.  The LAST clause always gets pause_ms=0 so the
    caller can assign the correct inter-sentence or block-level pause.

    When comma_ms <= 0 or there are no split points, returns [(sentence, 0)].
    """
    if comma_ms <= 0:
        return [(sentence, 0)]
    parts: list[str] = []
    last = 0
    for m in _CLAUSE_BOUNDARY_RE.finditer(sentence):
        # Split after the punctuation mark but before the whitespace,
        # so the mark stays with the preceding clause.
        split_at = m.start(2)  # first char of the whitespace run
        parts.append(sentence[last:split_at])
        last = m.end(2)        # resume after the whitespace
    if last < len(sentence):
        parts.append(sentence[last:])
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return [(sentence, 0)]
    return [(p, comma_ms) for p in parts[:-1]] + [(parts[-1], 0)]


def build_script(blocks: list[Block], cfg: dict[str, Any]) -> list[Chunk]:
    """Turn parsed blocks into a flat speech script with prosody."""
    headers_cfg = cfg.get("headers", {})
    pauses_cfg = cfg.get("pauses", {})
    code_cfg = cfg.get("code_blocks", {})

    h_rate = float(headers_cfg.get("rate_factor", 0.85))
    h_pause_before = int(headers_cfg.get("pause_before_ms", 500))
    h_pause_after = int(headers_cfg.get("pause_after_ms", 400))

    p_para = int(pauses_cfg.get("paragraph_ms", 350))
    p_list = int(pauses_cfg.get("list_item_ms", 200))
    p_hr = int(pauses_cfg.get("horizontal_rule_ms", 600))
    comma_ms = int(pauses_cfg.get("comma_ms", 150))

    code_mode = code_cfg.get("mode", "skip")
    announce_template = code_cfg.get("announce_template", "code block, {lines} lines")

    chunks: list[Chunk] = []

    for block in blocks:
        if block.kind == "header":
            if not block.text:
                continue
            chunks.append(
                Chunk(
                    text=block.text,
                    kind="header",
                    rate_factor=h_rate,
                    pause_before_ms=h_pause_before,
                    pause_after_ms=h_pause_after,
                )
            )

        elif block.kind == "paragraph":
            sentences = _split_sentences(block.text)
            for idx, sent in enumerate(sentences):
                last_sent = idx == len(sentences) - 1
                terminal_pause = p_para if last_sent else 0
                clauses = _split_clauses(sent, comma_ms)
                for c_idx, (clause_text, clause_pause) in enumerate(clauses):
                    last_clause = c_idx == len(clauses) - 1
                    chunks.append(
                        Chunk(
                            text=clause_text,
                            kind="paragraph",
                            rate_factor=1.0,
                            pause_after_ms=terminal_pause if last_clause else clause_pause,
                        )
                    )

        elif block.kind == "list_item":
            if not block.text:
                continue
            sentences = _split_sentences(block.text) or [block.text]
            for idx, sent in enumerate(sentences):
                last_sent = idx == len(sentences) - 1
                terminal_pause = p_list if last_sent else 0
                clauses = _split_clauses(sent, comma_ms)
                for c_idx, (clause_text, clause_pause) in enumerate(clauses):
                    last_clause = c_idx == len(clauses) - 1
                    chunks.append(
                        Chunk(
                            text=clause_text,
                            kind="list_item",
                            rate_factor=1.0,
                            pause_after_ms=terminal_pause if last_clause else clause_pause,
                        )
                    )

        elif block.kind == "blockquote":
            if not block.text:
                continue
            sentences_bq = _split_sentences(block.text) or [block.text]
            for idx_bq, sent in enumerate(sentences_bq):
                clauses = _split_clauses(sent, comma_ms)
                for c_idx, (clause_text, clause_pause) in enumerate(clauses):
                    last_clause = c_idx == len(clauses) - 1
                    chunks.append(
                        Chunk(
                            text=clause_text,
                            kind="blockquote",
                            pause_after_ms=p_para if last_clause else clause_pause,
                        )
                    )

        elif block.kind == "hr":
            chunks.append(Chunk(text="", kind="hr", pause_after_ms=p_hr))

        elif block.kind == "table":
            chunks.extend(_table_chunks(block, p_list))

        elif block.kind == "code":
            n_lines = len(block.lines)
            if code_mode == "silent-skip":
                continue
            if code_mode == "read":
                body = "\n".join(block.lines).strip()
                if body:
                    chunks.append(
                        Chunk(text=body, kind="code", pause_after_ms=p_para)
                    )
                continue
            # default: skip with announcement
            announce = announce_template.format(lines=n_lines)
            chunks.append(
                Chunk(
                    text=announce,
                    kind="code_announce",
                    pause_after_ms=p_para,
                )
            )

    return chunks


def _table_chunks(block: Block, pause_ms: int) -> list[Chunk]:
    """Read a table row-wise as 'col-header: value, ...'."""
    rows = block.rows
    if not rows:
        return []
    header = rows[0]
    body = rows[1:]
    out: list[Chunk] = []
    if not body:
        # No data rows; just read the header cells as a line.
        out.append(
            Chunk(text=", ".join(c for c in header if c), kind="table", pause_after_ms=pause_ms)
        )
        return out
    for row in body:
        pairs = []
        for ci, cell in enumerate(row):
            if not cell:
                continue
            col = header[ci] if ci < len(header) else ""
            pairs.append(f"{col}: {cell}" if col else cell)
        if pairs:
            out.append(
                Chunk(text=", ".join(pairs), kind="table", pause_after_ms=pause_ms)
            )
    return out


def make_script(text: str, cfg: dict[str, Any]) -> list[Chunk]:
    """Convenience: parse + build_script in one call. (clean happens upstream)"""
    return build_script(parse(text, cfg), cfg)
