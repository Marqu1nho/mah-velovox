"""Newline-delimited JSON protocol for speakwrite streaming.

Wire format:
  partial  -> {"text": "...", "volatile": true|false}
  final    -> {"event": "final", "text": "..."}
  done     -> {"event": "done"}

All messages are terminated with a newline.
"""

from __future__ import annotations

import json


def encode_partial(text: str, volatile: bool) -> str:
    """Encode a partial transcript event as an NDJSON line."""
    return json.dumps({"text": text, "volatile": volatile}) + "\n"


def encode_final(text: str) -> str:
    """Encode a final (polished) transcript event as an NDJSON line."""
    return json.dumps({"event": "final", "text": text}) + "\n"


def encode_done() -> str:
    """Encode a done sentinel event as an NDJSON line."""
    return json.dumps({"event": "done"}) + "\n"


def parse_line(line: str) -> dict | None:
    """Parse one NDJSON line. Returns None on blank or malformed input, never raises."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


class LineBuffer:
    """Accumulates streaming text chunks, returns parsed dicts for complete lines.

    Input may arrive split mid-line. Call feed() with each chunk; it returns a
    list of parsed objects for every complete line received so far. Partial
    remainder is retained internally until the next newline arrives.
    """

    def __init__(self) -> None:
        self._buf: str = ""

    def feed(self, chunk: str) -> list[dict]:
        """Append ``chunk`` to the internal buffer; return parsed objects for complete lines."""
        self._buf += chunk
        results: list[dict] = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            parsed = parse_line(line)
            if parsed is not None:
                results.append(parsed)
        return results
