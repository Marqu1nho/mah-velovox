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


# Sentence splitter: split on ., !, ? followed by whitespace, keeping the
# delimiter. Avoids splitting common abbreviations minimally.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = _SENT_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


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
                last = idx == len(sentences) - 1
                chunks.append(
                    Chunk(
                        text=sent,
                        kind="paragraph",
                        rate_factor=1.0,
                        pause_after_ms=p_para if last else 0,
                    )
                )

        elif block.kind == "list_item":
            if not block.text:
                continue
            sentences = _split_sentences(block.text) or [block.text]
            for idx, sent in enumerate(sentences):
                last = idx == len(sentences) - 1
                chunks.append(
                    Chunk(
                        text=sent,
                        kind="list_item",
                        rate_factor=1.0,
                        pause_after_ms=p_list if last else 0,
                    )
                )

        elif block.kind == "blockquote":
            if not block.text:
                continue
            for sent in _split_sentences(block.text) or [block.text]:
                chunks.append(
                    Chunk(text=sent, kind="blockquote", pause_after_ms=p_para)
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
