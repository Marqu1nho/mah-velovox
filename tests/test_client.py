"""Tests for the --send client path in __main__.py.

All tests are deterministic and fast: no real daemon, no real socket,
no real subprocess. We monkeypatch at the module level.
"""

from __future__ import annotations

import io
import json
import socket
import sys

import pytest

import readaloud.__main__ as main_mod
from readaloud.__main__ import main


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

class _FakeSocket:
    """Minimal socket stand-in: pre-loaded with response bytes."""

    def __init__(self, response_bytes: bytes = b""):
        self._send_buf = b""
        self._response = io.BytesIO(response_bytes)
        self._closed = False

    def connect(self, addr):
        pass

    def sendall(self, data: bytes):
        self._send_buf += data

    def makefile(self, mode):
        return self._response

    def close(self):
        self._closed = True


# ---------------------------------------------------------------------------
# (a) read blocks until {"event":"done"}
# ---------------------------------------------------------------------------

def test_send_read_blocks_until_done(monkeypatch, capsys):
    """_send_to_daemon('read', None) returns 0 after receiving event:done."""
    done_resp = b'{"event":"done"}\n'

    import socket as _socket

    class PatchedSocket:
        def __init__(self, *a, **kw):
            pass
        def connect(self, addr):
            pass
        def sendall(self, data):
            pass
        def makefile(self, mode):
            return io.BytesIO(done_resp)
        def close(self):
            pass

    monkeypatch.setattr(_socket, "socket", PatchedSocket)
    monkeypatch.setattr(main_mod, "_read_stdin", lambda: "hello world")
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/test-xdg-state")

    code = main_mod._send_to_daemon("read", None)
    assert code == 0


# ---------------------------------------------------------------------------
# (b) pause/stop send correct JSON and return 0
# ---------------------------------------------------------------------------

def test_send_pause_sends_correct_json(monkeypatch):
    ok_resp = b'{"ok":true}\n'
    import socket as _socket

    sent_data = []

    class PatchedSocket:
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

    monkeypatch.setattr(_socket, "socket", PatchedSocket)
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/test-xdg-state")

    code = main_mod._send_to_daemon("pause", None)
    assert code == 0
    combined = b"".join(sent_data)
    msg = json.loads(combined.decode().strip())
    assert msg == {"cmd": "pause"}


def test_send_stop_sends_correct_json(monkeypatch):
    ok_resp = b'{"ok":true}\n'
    import socket as _socket

    sent_data = []

    class PatchedSocket:
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

    monkeypatch.setattr(_socket, "socket", PatchedSocket)
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/test-xdg-state")

    code = main_mod._send_to_daemon("stop", None)
    assert code == 0
    combined = b"".join(sent_data)
    msg = json.loads(combined.decode().strip())
    assert msg == {"cmd": "stop"}


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

    code = main_mod._send_to_daemon("pause", None)
    # Popen should have been called.
    assert len(popen_calls) >= 1
    assert popen_calls[0].get("start_new_session") is True


# ---------------------------------------------------------------------------
# (d) the `send` Typer subcommands route to _send_to_daemon correctly
# ---------------------------------------------------------------------------

def _capture_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main_mod, "_send_to_daemon", lambda cmd, app: calls.append((cmd, app)) or 0
    )
    return calls


def test_send_read_command_routes(monkeypatch):
    from typer.testing import CliRunner

    calls = _capture_calls(monkeypatch)
    result = CliRunner().invoke(main_mod.app, ["send", "read"])
    assert result.exit_code == 0
    assert calls == [("read", None)]


def test_send_read_command_passes_app(monkeypatch):
    from typer.testing import CliRunner

    calls = _capture_calls(monkeypatch)
    result = CliRunner().invoke(main_mod.app, ["send", "read", "--app", "Safari"])
    assert result.exit_code == 0
    assert calls == [("read", "Safari")]


def test_send_pause_command_routes(monkeypatch):
    from typer.testing import CliRunner

    calls = _capture_calls(monkeypatch)
    result = CliRunner().invoke(main_mod.app, ["send", "pause"])
    assert result.exit_code == 0
    assert calls == [("pause", None)]


def test_send_stop_command_routes(monkeypatch):
    from typer.testing import CliRunner

    calls = _capture_calls(monkeypatch)
    result = CliRunner().invoke(main_mod.app, ["send", "stop"])
    assert result.exit_code == 0
    assert calls == [("stop", None)]
