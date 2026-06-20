"""Integration tests for the kokoro daemon over a real unix socket.

No real audio device: sounddevice is monkeypatched. No real model: FakeKokoro
is injected. The isolated_xdg autouse fixture (conftest.py) puts socket_path
and pidfile_path under a tmp dir automatically.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import numpy as np
import pytest

import sounddevice
from readaloud.config import DEFAULTS
import readaloud.engines.kokoro_engine as ke
from readaloud.daemon import Daemon, socket_path


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeKokoro:
    def create(self, text, voice, speed, lang):
        return np.zeros(2400, dtype=np.float32), 24000


class FakeStream:
    """Minimal sounddevice.OutputStream stand-in."""

    def __init__(self, *args, **kwargs):
        self._active = False

    def start(self):
        self._active = True

    def stop(self):
        self._active = False

    def close(self):
        self._active = False

    def abort(self):
        self._active = False

    def write(self, data):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ---------------------------------------------------------------------------
# Daemon fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def live_daemon(monkeypatch, tmp_path):
    """Start a Daemon with FakeKokoro and mocked sounddevice in a background thread."""
    # AF_UNIX path limit on macOS is 104 chars; pytest's tmp_path is too long.
    # Use a short path under /tmp instead.
    import tempfile
    short_state = tempfile.mkdtemp(prefix="rad-")
    monkeypatch.setenv("XDG_STATE_HOME", short_state)

    monkeypatch.setattr(sounddevice, "OutputStream", FakeStream)

    d = Daemon(model=FakeKokoro())
    # Run in a daemon thread so it exits when the test process exits.
    t = threading.Thread(target=d.run, daemon=True)
    t.start()

    # Wait for the daemon socket to appear (up to 3s).
    sp = str(socket_path())
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sp)
            s.close()
            break
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.05)
    else:
        pytest.fail("daemon socket never appeared")

    yield d

    d.stop()
    t.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send(cmd_dict: dict) -> dict:
    """Open a fresh connection, send one command, read one response line."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(socket_path()))
    s.sendall((json.dumps(cmd_dict) + "\n").encode())
    line = s.makefile("rb").readline()
    s.close()
    return json.loads(line.decode())


def _send_read(text: str) -> "tuple[socket.socket, object]":
    """Send a read command; return (open_socket, fileobj) for reading events."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(socket_path()))
    s.sendall((json.dumps({"cmd": "read", "text": text, "app": None}) + "\n").encode())
    return s, s.makefile("rb")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ping(live_daemon):
    resp = _send({"cmd": "ping"})
    assert resp == {"ok": True}


def test_read_returns_done(live_daemon):
    s, f = _send_read("Hello world.")
    deadline = time.monotonic() + 5.0
    got_done = False
    while time.monotonic() < deadline:
        line = f.readline()
        if not line:
            break
        msg = json.loads(line.decode())
        if msg.get("event") == "done":
            got_done = True
            break
    s.close()
    assert got_done


def test_pause_returns_ok(live_daemon):
    resp = _send({"cmd": "pause"})
    assert resp.get("ok") is True


def test_stop_returns_ok(live_daemon):
    resp = _send({"cmd": "stop"})
    assert resp.get("ok") is True


def test_malformed_json_returns_error(live_daemon):
    """Malformed JSON gets an error response; daemon stays alive."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(socket_path()))
    s.sendall(b"not valid json\n")
    line = s.makefile("rb").readline()
    s.close()
    resp = json.loads(line.decode())
    assert resp.get("ok") is False
    assert "error" in resp

    # Daemon still alive.
    assert _send({"cmd": "ping"}) == {"ok": True}


def test_unknown_cmd_returns_error(live_daemon):
    resp = _send({"cmd": "frobnicate"})
    assert resp.get("ok") is False
    assert "error" in resp
    # Daemon still alive.
    assert _send({"cmd": "ping"}) == {"ok": True}


def test_second_read_preempts_first(live_daemon):
    """Sending a second read while one is in flight preempts the first."""
    # Start a first read (long text so it's still in flight).
    s1, f1 = _send_read("First read " * 50)

    # Give the daemon a moment to start the first read.
    time.sleep(0.1)

    # Send second read — should preempt.
    s2, f2 = _send_read("Second read.")

    # Second read should finish.
    deadline = time.monotonic() + 5.0
    got_done2 = False
    while time.monotonic() < deadline:
        line = f2.readline()
        if not line:
            break
        msg = json.loads(line.decode())
        if msg.get("event") == "done":
            got_done2 = True
            break
    s2.close()

    # First connection gets closed (done or EOF).
    s1.settimeout(2.0)
    try:
        line = f1.readline()
        if line:
            msg = json.loads(line.decode())
            # Either done event or connection closed is fine.
    except Exception:
        pass
    s1.close()

    assert got_done2
    # Daemon still alive.
    assert _send({"cmd": "ping"}) == {"ok": True}
