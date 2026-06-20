"""Base types for speakwrite engines."""

from __future__ import annotations

import threading
import queue
from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable

import numpy as np  # noqa: F401 — type hint only


@dataclass(frozen=True)
class Partial:
    """A partial transcript result from a streaming engine.

    volatile=True  -> provisional, may be revised by the next Partial
    volatile=False -> committed, will not be revised
    """

    text: str
    volatile: bool  # True=provisional, False=committed


@runtime_checkable
class Engine(Protocol):
    """Protocol that all speakwrite engines must satisfy."""

    name: str
    sample_rate: int

    def stream(
        self,
        frames: "queue.Queue[np.ndarray | None]",
        stop: threading.Event,
    ) -> Iterator[Partial]:
        """Yield Partial results from streaming audio.

        ``frames`` is a queue of numpy audio frames (dtype float32, shape (N,)).
        A ``None`` sentinel signals end-of-stream.
        ``stop`` is set by the caller to request early termination.
        """
        ...

    def final(self) -> str:
        """Return the joined committed transcript after stream() is exhausted."""
        ...
