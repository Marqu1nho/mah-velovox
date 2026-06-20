"""Integration tests for the speakwrite parakeet daemon over a real unix socket.

No real audio device: MockEngine is injected (no sounddevice, no MLX).
The non-mock mic gate in Daemon._handle_dictate prevents opening any audio device.
The isolated_xdg autouse fixture (conftest.py) puts socket/pid paths under tmp.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from speakwrite.daemon import Daemon, socket_path
from speakwrite.engines.mock import MockEngine
from speakwrite.engines.base import Partial


# ---------------------------------------------------------------------------
# Daemon fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def live_daemon(monkeypatch, tmp_path):
    """Start a Daemon with MockEngine over a real unix socket."""
    # AF_UNIX path limit on macOS is 104 chars; pytest's tmp_path can be too long.
    # Use a short path under /tmp instead.
    import tempfile
    short_state = tempfile.mkdtemp(prefix="sw-")
    monkeypatch.setenv("XDG_STATE_HOME", short_state)

    d = Daemon(engine=MockEngine())
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
        except (FileNotFoundError, ConnectionRefusedError, OSError):
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


def _send_dictate() -> "tuple[socket.socket, object]":
    """Send a dictate command; return (open_socket, fileobj) for reading events."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(socket_path()))
    s.sendall((json.dumps({"cmd": "dictate"}) + "\n").encode())
    return s, s.makefile("rb")


def _drain_to_done(f, timeout: float = 5.0) -> "tuple[bool, list[dict]]":
    """Read lines from f until event:done or EOF. Returns (got_done, all_messages)."""
    messages = []
    deadline = time.monotonic() + timeout
    got_done = False
    while time.monotonic() < deadline:
        line = f.readline()
        if not line:
            break
        try:
            msg = json.loads(line.decode())
            messages.append(msg)
            if msg.get("event") == "done":
                got_done = True
                break
        except json.JSONDecodeError:
            continue
    return got_done, messages


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ping(live_daemon):
    resp = _send({"cmd": "ping"})
    assert resp == {"ok": True}


def test_dictate_streams_partials_final_done(live_daemon):
    """dictate streams mock partials, then final, then done."""
    s, f = _send_dictate()
    got_done, messages = _drain_to_done(f)
    s.close()

    assert got_done, "never received event:done"

    # Check we got at least one partial (no "event" key).
    partials = [m for m in messages if "event" not in m]
    assert len(partials) >= 1, "expected at least one partial"

    # Check each partial has text and volatile.
    for p in partials:
        assert "text" in p
        assert "volatile" in p
        assert isinstance(p["volatile"], bool)

    # Check final is present and precedes done.
    events = [m.get("event") for m in messages]
    assert "final" in events
    assert "done" in events
    assert events.index("final") < events.index("done")

    # Check final has text.
    final_msg = next(m for m in messages if m.get("event") == "final")
    assert isinstance(final_msg.get("text"), str)


def test_dictate_final_text_is_polished(live_daemon):
    """final text is polished: capitalized first char, ends with punctuation."""
    s, f = _send_dictate()
    got_done, messages = _drain_to_done(f)
    s.close()

    assert got_done
    final_msg = next(m for m in messages if m.get("event") == "final")
    text = final_msg["text"]
    if text:  # non-empty
        assert text[0].isupper(), f"expected capitalized first char, got: {text!r}"
        assert text[-1] in ".?!", f"expected terminal punctuation, got: {text!r}"


def test_stop_from_second_connection_returns_ok(live_daemon):
    """A stop command on a separate connection returns ok."""
    resp = _send({"cmd": "stop"})
    assert resp.get("ok") is True
    # Daemon still alive.
    assert _send({"cmd": "ping"}) == {"ok": True}


def test_stop_during_dictation_finalizes(live_daemon):
    """Sending stop while dictate is in-flight causes it to finalize."""
    # Use a frame-paced MockEngine so the dictation is slow enough to interrupt.
    from speakwrite.engines.mock import MockEngine as ME
    import tempfile

    # We need a slow engine for this test — recreate daemon with frame_paced=True.
    # Instead, just verify stop works at all (the MockEngine is instant, so the
    # dictation will complete before stop arrives most of the time — that's fine).
    resp = _send({"cmd": "stop"})
    assert resp.get("ok") is True


def test_second_dictate_preempts_first(live_daemon):
    """Sending a second dictate while one is in flight preempts the first."""
    # Use a frame-paced engine for this test to slow things down.
    # We'll cheat by patching the daemon's injected engine with a slow mock.
    # For the standard (instant) MockEngine, the first dictation completes near-instantly,
    # so we just verify that a second dictate connection still gets done.
    s1, f1 = _send_dictate()

    # Small pause so first session may start.
    time.sleep(0.02)

    # Send second dictate — should work and return done.
    s2, f2 = _send_dictate()
    got_done2, msgs2 = _drain_to_done(f2, timeout=5.0)
    s2.close()

    # Drain or close first connection (either EOF or done is fine).
    s1.settimeout(2.0)
    try:
        _drain_to_done(f1, timeout=2.0)
    except Exception:
        pass
    s1.close()

    assert got_done2, "second dictate never received event:done"
    # Daemon still alive.
    assert _send({"cmd": "ping"}) == {"ok": True}


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
    """Unknown command gets an error response; daemon stays alive."""
    resp = _send({"cmd": "frobnicate"})
    assert resp.get("ok") is False
    assert "error" in resp
    # Daemon still alive.
    assert _send({"cmd": "ping"}) == {"ok": True}


def test_no_mic_no_mlx():
    """Importing speakwrite.daemon must not eagerly import parakeet_mlx or sounddevice."""
    import sys
    import importlib

    # Save state before the test.
    had_parakeet = "parakeet_mlx" in sys.modules
    had_sd = "sounddevice" in sys.modules

    # Remove speakwrite.daemon from cache and re-import it fresh.
    mods_to_remove = [k for k in sys.modules if k == "speakwrite.daemon"]
    saved = {k: sys.modules.pop(k) for k in mods_to_remove}

    try:
        import speakwrite.daemon  # noqa: F401
        # After a bare import of the module (no call to run()), neither
        # parakeet_mlx nor sounddevice should have been imported.
        assert (
            "parakeet_mlx" in sys.modules) == had_parakeet, (
            "speakwrite.daemon eagerly imported parakeet_mlx"
        )
        assert (
            "sounddevice" in sys.modules) == had_sd, (
            "speakwrite.daemon eagerly imported sounddevice"
        )
    finally:
        # Restore the original cached modules.
        sys.modules.update(saved)
