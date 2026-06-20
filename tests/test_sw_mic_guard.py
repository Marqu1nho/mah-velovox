"""Unit tests for speakwrite/capture/mic.py — mic guard helpers.

No real InputStream is opened; only the pure-Python helpers are exercised.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from speakwrite.capture.mic import looks_silent, mic_permission_status, rms


# ---------------------------------------------------------------------------
# rms()
# ---------------------------------------------------------------------------


def test_rms_all_zeros():
    arr = np.zeros(1024, dtype=np.float32)
    assert rms(arr) == 0.0


def test_rms_empty():
    arr = np.zeros(0, dtype=np.float32)
    assert rms(arr) == 0.0


def test_rms_constant_one():
    arr = np.ones(512, dtype=np.float32)
    assert abs(rms(arr) - 1.0) < 1e-6


def test_rms_sine():
    """RMS of a full-amplitude sine should be 1/sqrt(2) ≈ 0.707."""
    t = np.linspace(0, 2 * math.pi, 16000, endpoint=False)
    arr = np.sin(t).astype(np.float32)
    expected = 1.0 / math.sqrt(2)
    assert abs(rms(arr) - expected) < 1e-3


def test_rms_negative_values():
    """RMS is always non-negative."""
    arr = np.full(256, -0.5, dtype=np.float32)
    assert rms(arr) == pytest.approx(0.5, abs=1e-6)


def test_rms_mixed():
    arr = np.array([1.0, -1.0, 1.0, -1.0], dtype=np.float32)
    assert rms(arr) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# looks_silent()
# ---------------------------------------------------------------------------


def test_looks_silent_zeros():
    arr = np.zeros(1024, dtype=np.float32)
    assert looks_silent(arr) is True


def test_looks_silent_sine_loud():
    t = np.linspace(0, 2 * math.pi, 16000, endpoint=False)
    arr = np.sin(t).astype(np.float32)
    assert looks_silent(arr) is False


def test_looks_silent_noise():
    rng = np.random.default_rng(42)
    arr = rng.uniform(-0.5, 0.5, 4096).astype(np.float32)
    assert looks_silent(arr) is False


def test_looks_silent_very_quiet():
    """A signal just below threshold is treated as silent."""
    arr = np.full(1024, 5e-5, dtype=np.float32)
    assert looks_silent(arr, threshold=1e-4) is True


def test_looks_silent_just_above_threshold():
    arr = np.full(1024, 2e-4, dtype=np.float32)
    assert looks_silent(arr, threshold=1e-4) is False


def test_looks_silent_custom_threshold():
    arr = np.full(512, 0.01, dtype=np.float32)
    assert looks_silent(arr, threshold=0.02) is True
    assert looks_silent(arr, threshold=0.005) is False


# ---------------------------------------------------------------------------
# mic_permission_status()
# ---------------------------------------------------------------------------


def test_permission_status_returns_string():
    """Always returns a string, never raises."""
    result = mic_permission_status()
    assert isinstance(result, str)


def test_permission_status_known_values():
    """Must be one of the documented values (AVFoundation may be absent → 'unknown')."""
    allowed = {"authorized", "denied", "restricted", "not_determined", "unknown"}
    assert mic_permission_status() in allowed


def test_permission_status_no_crash_without_avfoundation(monkeypatch):
    """Even if the import fails, the function returns a string."""
    import builtins
    real_import = builtins.__import__

    def _mock_import(name, *args, **kwargs):
        if name in ("AVFoundation",):
            raise ImportError("not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _mock_import)
    result = mic_permission_status()
    assert isinstance(result, str)
    assert result == "unknown"
