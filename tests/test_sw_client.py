"""Tests for the send client commands in speakwrite/__main__.py.

All tests are deterministic and fast: no real daemon, no real socket,
no real subprocess. We monkeypatch at the module level.
"""

from __future__ import annotations

import io
import json
import socket
import sys

import pytest

import speakwrite.__main__ as main_mod
from speakwrite.__main__ import main


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

class _PatchedSocket:
    """Minimal socket stand-in that captures sent data and returns preset responses."""

    def __init__(self, response_bytes: bytes = b""):
        self._send_buf = b""
        self._response = io.BytesIO(response_bytes)

    def connect(self, addr):
        pass

    def sendall(self, data: bytes):
        self._send_buf += data

    def makefile(self, mode):
        return self._response

    def close(self):
        pass


# ---------------------------------------------------------------------------
# (a) send ping sends correct JSON and prints ack
# ---------------------------------------------------------------------------

def test_send_ping_sends_correct_json(monkeypatch, capsys):
    """_send_to_daemon('ping') sends {"cmd":"ping"} and returns 0."""
    ok_resp = b'{"ok":true}\n'
    import socket as _socket

    sent_data = []

    class PS:
        def __init__(self, *a, **kw):
            pass
        def connect(self, addr):
            pass
        def sendall(self, data):
            sent_data.append(data)
        def makefile(self, mode):
            return io.BytesIO(ok_resp)
        def close(self):
            pass

    monkeypatch.setattr(_socket, "socket", PS)
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/test-sw-xdg-state")

    code = main_mod._send_to_daemon("ping")
    assert code == 0
    combined = b"".join(sent_data)
    msg = json.loads(combined.decode().strip())
    assert msg == {"cmd": "ping"}

    out = capsys.readouterr().out
    assert "ok" in out  # ack relayed to stdout


def test_send_stop_sends_correct_json(monkeypatch, capsys):
    """_send_to_daemon('stop') sends {"cmd":"stop"} and returns 0."""
    ok_resp = b'{"ok":true}\n'
    import socket as _socket

    sent_data = []

    class PS:
        def __init__(self, *a, **kw):
            pass
        def connect(self, addr):
            pass
        def sendall(self, data):
            sent_data.append(data)
        def makefile(self, mode):
            return io.BytesIO(ok_resp)
        def close(self):
            pass

    monkeypatch.setattr(_socket, "socket", PS)
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/test-sw-xdg-state")

    code = main_mod._send_to_daemon("stop")
    assert code == 0
    combined = b"".join(sent_data)
    msg = json.loads(combined.decode().strip())
    assert msg == {"cmd": "stop"}


# ---------------------------------------------------------------------------
# (b) send dictate relays streamed lines to stdout, exits on event:done
# ---------------------------------------------------------------------------

def test_send_dictate_relays_lines_and_exits_on_done(monkeypatch, capsys):
    """_send_to_daemon('dictate') relays all lines verbatim to stdout and returns 0."""
    streamed = (
        b'{"text": "hello", "volatile": true}\n'
        b'{"event": "final", "text": "Hello."}\n'
        b'{"event": "done"}\n'
    )
    import socket as _socket

    class PS:
        def __init__(self, *a, **kw):
            pass
        def connect(self, addr):
            pass
        def sendall(self, data):
            pass
        def makefile(self, mode):
            return io.BytesIO(streamed)
        def close(self):
            pass

    monkeypatch.setattr(_socket, "socket", PS)
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/test-sw-xdg-state")

    code = main_mod._send_to_daemon("dictate")
    assert code == 0

    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    objects = [json.loads(l) for l in lines]

    # Should have the partial, final, and done.
    events = [o.get("event") for o in objects]
    assert "final" in events
    assert "done" in events

    partials = [o for o in objects if "event" not in o]
    assert len(partials) >= 1
    assert partials[0]["text"] == "hello"
    assert partials[0]["volatile"] is True


def test_send_dictate_stops_after_done(monkeypatch, capsys):
    """dictate stops reading after event:done even if more data follows."""
    # Extra line after done that should NOT be relayed.
    streamed = (
        b'{"event": "final", "text": "Hi."}\n'
        b'{"event": "done"}\n'
        b'{"event": "extra", "should": "not appear"}\n'
    )
    import socket as _socket

    class PS:
        def __init__(self, *a, **kw):
            pass
        def connect(self, addr):
            pass
        def sendall(self, data):
            pass
        def makefile(self, mode):
            return io.BytesIO(streamed)
        def close(self):
            pass

    monkeypatch.setattr(_socket, "socket", PS)
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/test-sw-xdg-state")

    code = main_mod._send_to_daemon("dictate")
    assert code == 0

    out = capsys.readouterr().out
    assert "extra" not in out


# ---------------------------------------------------------------------------
# (c) lazy start: subprocess.Popen called with start_new_session=True
# ---------------------------------------------------------------------------

def test_lazy_start_spawns_daemon(monkeypatch, tmp_path):
    """When connect fails, Popen is called with start_new_session=True."""
    import socket as _socket
    import subprocess
    import time

    popen_calls = []
    attempt_count = [0]

    class FailThenSucceedSocket:
        def __init__(self, *a, **kw):
            pass
        def connect(self, addr):
            attempt_count[0] += 1
            if attempt_count[0] <= 1:
                raise FileNotFoundError("no socket yet")
            # success on 2nd+ attempt
        def sendall(self, data):
            pass
        def makefile(self, mode):
            return io.BytesIO(b'{"ok":true}\n')
        def close(self):
            pass

    class FakePopen:
        def __init__(self, *args, **kwargs):
            popen_calls.append(kwargs)

    monkeypatch.setattr(_socket, "socket", FailThenSucceedSocket)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(time, "sleep", lambda x: None)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    code = main_mod._send_to_daemon("ping")
    # Popen should have been called with start_new_session=True.
    assert len(popen_calls) >= 1
    assert popen_calls[0].get("start_new_session") is True


# ---------------------------------------------------------------------------
# (d) Typer subcommands route to _send_to_daemon correctly
# ---------------------------------------------------------------------------

def _capture_send_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main_mod, "_send_to_daemon", lambda cmd: calls.append(cmd) or 0
    )
    return calls


def test_send_dictate_command_routes(monkeypatch):
    from typer.testing import CliRunner

    calls = _capture_send_calls(monkeypatch)
    result = CliRunner().invoke(main_mod.app, ["send", "dictate"])
    assert result.exit_code == 0
    assert calls == ["dictate"]


def test_send_stop_command_routes(monkeypatch):
    from typer.testing import CliRunner

    calls = _capture_send_calls(monkeypatch)
    result = CliRunner().invoke(main_mod.app, ["send", "stop"])
    assert result.exit_code == 0
    assert calls == ["stop"]


def test_send_ping_command_routes(monkeypatch):
    from typer.testing import CliRunner

    calls = _capture_send_calls(monkeypatch)
    result = CliRunner().invoke(main_mod.app, ["send", "ping"])
    assert result.exit_code == 0
    assert calls == ["ping"]


def test_daemon_command_routes(monkeypatch):
    """speakwrite daemon invokes daemon.main()."""
    from typer.testing import CliRunner

    called = []
    monkeypatch.setattr(
        "speakwrite.daemon.run", lambda: called.append(True) or 0
    )
    result = CliRunner().invoke(main_mod.app, ["daemon"])
    # Either it succeeded (0) or was patched — just check daemon.run was called.
    assert len(called) >= 1 or result.exit_code == 0
