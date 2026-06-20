"""Warm kokoro daemon — keeps the model loaded, serves reads over a unix socket.

Protocol: newline-delimited JSON, one request per connection.
  {"cmd":"ping"}                     -> {"ok":true}
  {"cmd":"read","text":"...","app":null|"name"} -> streaming: {"event":"done"} when finished
  {"cmd":"pause"}                    -> {"ok":true}
  {"cmd":"stop"}                     -> {"ok":true}
  unknown/malformed                  -> {"ok":false,"error":"..."}

Paths (all under XDG_STATE_HOME/readaloud or ~/.local/state/readaloud):
  daemon.sock  - unix socket
  daemon.pid   - pid file
  daemon.log   - client-started log (subprocess.Popen redirects here)

Phase 1: no idle timeout (daemon stays alive until killed or KeyboardInterrupt).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("readaloud.daemon")


# ---------------------------------------------------------------------------
# Path helpers (importable by __main__ and tests)
# ---------------------------------------------------------------------------

def state_dir() -> Path:
    """XDG_STATE_HOME/readaloud or ~/.local/state/readaloud."""
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "readaloud"
    return Path.home() / ".local" / "state" / "readaloud"


def socket_path() -> Path:
    return state_dir() / "daemon.sock"


def pidfile_path() -> Path:
    return state_dir() / "daemon.pid"


def daemon_log_path() -> Path:
    return state_dir() / "daemon.log"


# ---------------------------------------------------------------------------
# Pid helpers
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------------------
# Daemon class
# ---------------------------------------------------------------------------

class Daemon:
    def __init__(self, model=None):
        """model=None means load from disk; inject a fake for tests."""
        self._injected_model = model
        self.model = None  # set in run()
        self._current: Any = None  # currently-playing KokoroEngine | None
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._stop_flag = threading.Event()

    # ------------------------------------------------------------------
    # Single-instance locking
    # ------------------------------------------------------------------

    def _acquire_lock(self) -> bool:
        """Write our pid to pidfile. Returns False if another live daemon owns it."""
        pf = pidfile_path()
        pf.parent.mkdir(parents=True, exist_ok=True)
        if pf.exists():
            try:
                existing = int(pf.read_text().strip() or "0")
            except (ValueError, OSError):
                existing = 0
            if existing and existing != os.getpid() and _pid_alive(existing):
                return False
        pf.write_text(str(os.getpid()))
        return True

    def _release_lock(self) -> None:
        pf = pidfile_path()
        try:
            if pf.exists() and pf.read_text().strip() == str(os.getpid()):
                pf.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Connection handler
    # ------------------------------------------------------------------

    def _handle(self, conn: socket.socket) -> None:
        engine_for_this_read = None
        try:
            f = conn.makefile("rwb")
            line = f.readline()
            if not line:
                return
            try:
                req = json.loads(line.decode("utf-8", errors="replace").strip())
            except json.JSONDecodeError as exc:
                reply = json.dumps({"ok": False, "error": f"malformed JSON: {exc}"}) + "\n"
                try:
                    f.write(reply.encode())
                    f.flush()
                except OSError:
                    pass
                return

            cmd = req.get("cmd", "")

            if cmd == "ping":
                try:
                    f.write(b'{"ok":true}\n')
                    f.flush()
                except OSError:
                    pass

            elif cmd == "read":
                text = req.get("text", "")
                app = req.get("app") or None

                # 1. Preempt any running read.
                with self._lock:
                    old = self._current
                    if old is not None:
                        old.stop()
                        self._current = None
                # Wait (outside the lock) for the preempted playback to fully
                # tear down its audio stream before we start the next read.
                # The next read re-initializes PortAudio to refresh the device
                # list, which is only safe when no other stream is open.
                # Bounded timeout so a wedged old playback can't hang us.
                if old is not None:
                    try:
                        if not old.wait_finished(timeout=2.0):
                            log.warning(
                                "preempted playback did not finish within 2s; continuing"
                            )
                    except Exception:
                        pass

                # 2. Fresh config per read.
                from .config import load_config
                cfg = load_config()

                # 3. Clean + parse + build script.
                from .clean import clean
                from .parse import parse
                from .script import build_script
                cleaned = clean(text, cfg, app=app)
                chunks = build_script(parse(cleaned, cfg), cfg)
                if not chunks:
                    try:
                        f.write(b'{"event":"done"}\n')
                        f.flush()
                    except OSError:
                        pass
                    return

                # 4. Build engine with warm model.
                from .engines.kokoro_engine import KokoroEngine
                engine = KokoroEngine(cfg, model=self.model)
                engine_for_this_read = engine
                with self._lock:
                    self._current = engine

                # 5. Speak (blocks on this connection's thread).
                try:
                    engine.speak(chunks)
                except Exception as exc:
                    log.error("speak error: %s", exc)

                # 6. Clear current if still ours; send done.
                with self._lock:
                    if self._current is engine:
                        self._current = None
                try:
                    f.write(b'{"event":"done"}\n')
                    f.flush()
                except OSError:
                    pass

            elif cmd == "pause":
                with self._lock:
                    eng = self._current
                if eng is not None:
                    eng.toggle_pause()
                try:
                    f.write(b'{"ok":true}\n')
                    f.flush()
                except OSError:
                    pass

            elif cmd == "stop":
                with self._lock:
                    eng = self._current
                    self._current = None
                if eng is not None:
                    eng.stop()
                try:
                    f.write(b'{"ok":true}\n')
                    f.flush()
                except OSError:
                    pass

            else:
                reply = json.dumps({"ok": False, "error": f"unknown cmd: {cmd!r}"}) + "\n"
                try:
                    f.write(reply.encode())
                    f.flush()
                except OSError:
                    pass

        except Exception as exc:
            log.error("_handle error: %s", exc)
            # If there was an engine running for this read, stop it.
            if engine_for_this_read is not None:
                try:
                    engine_for_this_read.stop()
                except Exception:
                    pass
                with self._lock:
                    if self._current is engine_for_this_read:
                        self._current = None
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Accept loop + startup
    # ------------------------------------------------------------------

    def run(self) -> int:
        # Single-instance lock.
        if not self._acquire_lock():
            print("readaloud daemon: already running", file=sys.stderr)
            return 0

        # Remove stale socket file.
        sp = socket_path()
        try:
            sp.unlink()
        except FileNotFoundError:
            pass

        # Bind and listen.
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock = srv
        try:
            srv.bind(str(sp))
        except OSError as exc:
            log.error("bind failed: %s", exc)
            self._release_lock()
            return 1
        srv.listen(16)

        # Load (or inject) model.
        if self._injected_model is not None:
            self.model = self._injected_model
        else:
            from .engines.kokoro_engine import _load_kokoro
            log.info("loading kokoro model…")
            self.model = _load_kokoro()

        log.info("daemon ready")  # orchestrator greps for "daemon ready"
        print("readaloud daemon: daemon ready", file=sys.stderr)

        try:
            while not self._stop_flag.is_set():
                try:
                    srv.settimeout(1.0)
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_flag.is_set():
                        break
                    raise
                t = threading.Thread(target=self._handle, args=(conn,), daemon=True)
                t.start()
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()
        return 0

    def _shutdown(self) -> None:
        self._stop_flag.set()
        srv = self._sock
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        try:
            socket_path().unlink()
        except FileNotFoundError:
            pass
        self._release_lock()

    def stop(self) -> None:
        """Signal the run() loop to exit (used by tests)."""
        self._stop_flag.set()
        # Close the server socket so accept() unblocks immediately.
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run() -> int:
    d = Daemon()
    return d.run()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
