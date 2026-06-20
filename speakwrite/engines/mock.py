"""MockEngine — scripted STT engine for testing (no mic, no MLX)."""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Iterator

from .base import Partial

# Default scripted sequence: volatile → committed progression showing two sentences.
_DEFAULT_SCRIPT: list[Partial] = [
    Partial("the", True),
    Partial("the quick", True),
    Partial("the quick brown", True),
    Partial("the quick brown fox.", False),
    Partial("jumped", True),
    Partial("jumped over", True),
    Partial("jumped over the lazy dog.", False),
]


class MockEngine:
    """Scripted speech-to-text engine — emits a pre-set Partial sequence.

    No microphone, no MLX, no audio device required.

    Args:
        script:      Custom list of Partial objects to emit. Defaults to the
                     built-in two-sentence sequence.
        frame_paced: If True, insert a small sleep between yields to simulate
                     real-time audio pacing. If False (default), emit instantly
                     — best for unit tests.
    """

    name = "mock"
    sample_rate = 16000

    def __init__(
        self,
        script: list[Partial] | None = None,
        frame_paced: bool = False,
    ) -> None:
        self._script: list[Partial] = script if script is not None else _DEFAULT_SCRIPT
        self._frame_paced = frame_paced
        self._committed: list[str] = []

    def stream(
        self,
        frames: "queue.Queue[Any]",
        stop: threading.Event,
    ) -> Iterator[Partial]:
        """Yield the scripted Partial sequence, honoring stop between each yield."""
        self._committed = []
        for partial in self._script:
            if stop.is_set():
                break
            if self._frame_paced:
                time.sleep(0.05)
            # Record committed text BEFORE yielding so that early generator
            # close (caller breaks) still captures this item.
            if not partial.volatile:
                self._committed.append(partial.text)
            yield partial

    def final(self) -> str:
        """Return committed (volatile=False) text joined with a space."""
        return " ".join(self._committed)
