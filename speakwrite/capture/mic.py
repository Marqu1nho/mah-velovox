"""Microphone capture for speakwrite.

Provides:
  - MicCapture: opens a sounddevice InputStream and feeds frames into a Queue.
  - mic_permission_status(): query macOS AVFoundation for mic permission.
  - rms() / looks_silent(): energy helpers for detecting all-zero audio.

macOS Tahoe (26) note: a denied or regressed mic returns all-zero audio with
NO error from PortAudio.  Always check permission + RMS on the first second of
audio; fail loudly rather than transcribing silence.
"""

from __future__ import annotations

import logging
import math
import queue
from typing import Callable

import numpy as np

_log = logging.getLogger("speakwrite.mic")


# ---------------------------------------------------------------------------
# macOS permission guard
# ---------------------------------------------------------------------------


def mic_permission_status() -> str:
    """Return the macOS mic authorization status as a string.

    Returns one of: "authorized" / "denied" / "restricted" /
    "not_determined" / "unknown".

    Requires pyobjc-framework-AVFoundation.  If that package is absent (or
    this is Linux), returns "unknown" without raising.
    """
    try:
        import AVFoundation  # type: ignore[import]  # pyobjc — optional
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore[import]
    except Exception:
        return "unknown"

    try:
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        # 0=not_determined, 1=restricted, 2=denied, 3=authorized
        return {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}.get(
            status, "unknown"
        )
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# RMS / silence detection
# ---------------------------------------------------------------------------


def rms(frames: np.ndarray) -> float:
    """Return root-mean-square energy of a float32 frame array."""
    if len(frames) == 0:
        return 0.0
    return float(math.sqrt(float(np.mean(frames.astype(np.float64) ** 2))))


def looks_silent(frames: np.ndarray, threshold: float = 1e-4) -> bool:
    """Return True if the RMS energy of *frames* is below *threshold*.

    Used to detect all-zero audio returned by a denied/regressed mic on
    macOS Tahoe.
    """
    return rms(frames) < threshold


# ---------------------------------------------------------------------------
# MicCapture
# ---------------------------------------------------------------------------


class MicCapture:
    """Open a PortAudio input stream and push frames into a bounded Queue.

    Args:
        sample_rate: Desired capture sample rate (default 16 000 Hz).
        blocksize:   Frames per callback block (default 1 024).
        maxsize:     Max items in the frame queue before dropping.
        on_frame:    Optional extra callback invoked with each 1-D float32 array.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        blocksize: int = 1024,
        maxsize: int = 200,
        on_frame: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self._maxsize = maxsize
        self._on_frame = on_frame
        self._q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=maxsize)
        self._stream = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(self) -> "queue.Queue[np.ndarray | None]":
        """Open the PortAudio input stream and return the frame queue.

        Re-initializes PortAudio before opening so that device changes
        (headphones in/out, Bluetooth, sleep/wake) that happened while the
        process was running don't leave a stale device entry that causes
        "Internal PortAudio error".  The same pattern is used in
        readaloud/engines/kokoro_engine.py for the output stream.
        """
        # Lazy-import so importing this module on Linux (no sounddevice
        # installed) doesn't fail at import time.
        import sounddevice as sd  # type: ignore[import]

        # Flush PortAudio's cached device list so the current default input
        # device is visible even if it changed since the process started.
        try:
            sd._terminate()
        except Exception:
            pass  # not yet initialized — fine
        try:
            sd._initialize()
        except Exception as exc:
            _log.warning("PortAudio re-initialize failed: %s; continuing", exc)

        def _callback(indata, frames, time_info, status):  # noqa: ARG001
            if status:
                _log.debug("sounddevice status: %s", status)
            chunk = indata.copy().reshape(-1)  # ensure 1-D float32
            if self._on_frame is not None:
                try:
                    self._on_frame(chunk)
                except Exception:
                    pass
            try:
                self._q.put_nowait(chunk)
            except queue.Full:
                # NEVER block the audio callback — just drop.
                _log.debug("mic queue full; dropping frame")

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.blocksize,
            callback=_callback,
        )
        self._stream.start()
        return self._q

    def close(self) -> None:
        """Stop the stream and put the None sentinel so consumers can drain."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                _log.warning("error closing mic stream: %s", exc)
            self._stream = None
        # Sentinel so any consumer waiting on .get() can exit.
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "queue.Queue[np.ndarray | None]":
        return self.open()

    def __exit__(self, *_) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def open_mic_stream(
    sample_rate: int = 16000,
    blocksize: int = 1024,
    on_frame: Callable[[np.ndarray], None] | None = None,
) -> "tuple[MicCapture, queue.Queue[np.ndarray | None]]":
    """Open a mic stream and return *(capture, queue)*.

    The caller is responsible for calling ``capture.close()`` when done.
    Prefer the ``MicCapture`` context manager for automatic cleanup.
    """
    cap = MicCapture(sample_rate=sample_rate, blocksize=blocksize, on_frame=on_frame)
    q = cap.open()
    return cap, q
