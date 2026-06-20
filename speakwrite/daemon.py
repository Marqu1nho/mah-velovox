"""Warm parakeet daemon — keeps the model loaded, serves STT streams over a unix socket.

Protocol: newline-delimited JSON, one request per connection.
  {"cmd":"ping"}      -> {"ok":true}
  {"cmd":"dictate"}   -> streaming: partials, then {"event":"final","text":"..."}, then {"event":"done"}
  {"cmd":"stop"}      -> {"ok":true}  (finalizes any in-flight session)
  unknown/malformed   -> {"ok":false,"error":"..."}

Paths (all under XDG_STATE_HOME/speakwrite or ~/.local/state/speakwrite):
  daemon.sock  - unix socket
  daemon.pid   - pid file
  daemon.log   - client-started log (subprocess.Popen redirects here)

Phase 1: no idle timeout (daemon stays alive until killed or KeyboardInterrupt).

Threading model
---------------
parakeet-mlx binds a loaded model to the OS thread that called from_pretrained.
Calling add_audio from any other thread raises:
  RuntimeError: There is no Stream(gpu, 0) in current thread

Fix: one long-lived INFERENCE THREAD loads the model, warms it, and runs ALL
dictation inference.  Per-connection worker threads hand it a Job (audio queue +
stop event + output queue) and drain the output queue to relay results to the
socket.  The inference thread NEVER touches sockets; connection threads NEVER
touch the model.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("speakwrite.daemon")


# ---------------------------------------------------------------------------
# Path helpers (importable by __main__ and tests)
# ---------------------------------------------------------------------------

def state_dir() -> Path:
    """XDG_STATE_HOME/speakwrite or ~/.local/state/speakwrite."""
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "speakwrite"
    return Path.home() / ".local" / "state" / "speakwrite"


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
# Inference job
# ---------------------------------------------------------------------------

@dataclass
class Job:
    """A dictation job handed from a connection thread to the inference thread."""
    frames: queue.Queue          # Queue[np.ndarray | None] — audio frames
    stop: threading.Event        # set by 'stop' command or preemption
    out: queue.Queue = field(default_factory=queue.Queue)
    # out receives tuples:
    #   ("partial", text, volatile)
    #   ("final",   text)
    #   ("done",)
    # The inference thread always terminates a job with ("done",).


# ---------------------------------------------------------------------------
# Daemon class
# ---------------------------------------------------------------------------

class Daemon:
    def __init__(self, engine=None, model=None):
        """engine=None means build from config; inject a MockEngine for tests.
        model=None means load from disk; inject a fake parakeet model for tests.
        """
        self._injected_engine = engine
        self._injected_model = model
        self.model = None  # set on the inference thread

        # Current session: (stop_event, job) or None.
        self._current: Any = None   # (threading.Event, Job) | None
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._stop_flag = threading.Event()

        # Inference thread plumbing.
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()   # set once model+warmup done
        self._infer_thread: threading.Thread | None = None

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
    # Inference thread
    # ------------------------------------------------------------------

    def _infer_loop(self) -> None:
        """Long-lived inference thread: load model, warm up, then process jobs.

        ALL parakeet/MLX calls happen on this thread.  Connection threads
        only push Job objects and drain job.out — they never touch self.model.
        """
        from .config import load_config

        if self._injected_engine is not None:
            # Test path: engine already injected — skip real model load.
            self.model = self._injected_model
            self._ready.set()
        else:
            # Production: load the real parakeet model on THIS thread.
            from .engines.parakeet import _load_model, ParakeetEngine
            log.info("loading parakeet model…")
            try:
                self.model = _load_model()
            except Exception as exc:
                log.error("failed to load parakeet model: %s", exc)
                self._ready.set()  # unblock run() even on failure
                return
            log.info("parakeet model loaded; warming up…")
            try:
                warmup_cfg = load_config()
                warmup_engine = ParakeetEngine(warmup_cfg, model=self.model)
                warmup_engine.warmup()
            except Exception as exc:
                log.warning("pre-warmup failed (non-fatal): %s", exc)
            self._ready.set()

        # Process jobs until stop_flag is set.
        while not self._stop_flag.is_set():
            try:
                job: Job = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue

            # Build the engine for this job.
            try:
                if self._injected_engine is not None:
                    engine = self._injected_engine
                else:
                    from .engines.parakeet import ParakeetEngine
                    from .config import load_config as _lc
                    cfg = _lc()
                    engine = ParakeetEngine(cfg, model=self.model)

                from .polish import polish
                from .config import load_config as _lc2
                cfg = _lc2()

                for partial in engine.stream(job.frames, job.stop):
                    job.out.put(("partial", partial.text, partial.volatile))

                final_text = polish(engine.final(), cfg.get("polish", "punctuation"))
                job.out.put(("final", final_text))

            except Exception as exc:
                log.error("inference job error: %s", exc)
            finally:
                # Always signal completion so the connection thread unblocks.
                job.out.put(("done",))

    # ------------------------------------------------------------------
    # Connection handler
    # ------------------------------------------------------------------

    def _handle(self, conn: socket.socket) -> None:
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

            elif cmd == "dictate":
                self._handle_dictate(conn, f)

            elif cmd == "stop":
                # Signal the current session to finalize; it emits final+done
                # on its own connection. We reply ok on this (control) connection.
                with self._lock:
                    current = self._current
                    if current is not None:
                        current[0].set()  # set the stop event
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
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_dictate(self, conn: socket.socket, f) -> None:
        """Handle a dictate command on the connection thread.

        This method does NOT touch the model or run any MLX/parakeet code.
        It opens the mic (if real engine), submits a Job to the inference
        thread, and drains job.out to relay results to the socket.
        """
        # 1. Preempt any running session (set its stop; its job drains naturally).
        with self._lock:
            old = self._current
            if old is not None:
                old[0].set()  # signal stop event of old job
                self._current = None

        # 2. Fresh config per dictate.
        from .config import load_config
        cfg = load_config()

        # 3. Build stop event for this session.
        stop = threading.Event()

        # 4. Determine if we're in mock mode (no mic, no MLX).
        is_mock = (
            self._injected_engine is not None
            or cfg.get("engine") == "mock"
            or getattr(self._injected_engine, "name", None) == "mock"
        )

        cap = None
        if is_mock:
            # MockEngine ignores frames — pass a dummy sentinel queue.
            frames: queue.Queue = queue.Queue()
            frames.put(None)
        else:
            from .capture.mic import MicCapture, looks_silent
            import numpy as np

            cap = MicCapture(
                sample_rate=16000,
                blocksize=1024,
            )
            frames = cap.open()

            # First-second silence check (macOS Tahoe all-zero mic on denial).
            _silence_check_samples = 16000
            _first_audio: list = []
            _first_audio_len = 0
            _silence_checked = False

            def _on_frame_silence_check(chunk):
                nonlocal _first_audio_len, _silence_checked
                if not _silence_checked:
                    _first_audio.append(chunk)
                    _first_audio_len += len(chunk)
                    if _first_audio_len >= _silence_check_samples:
                        _silence_checked = True
                        first_block = np.concatenate(_first_audio)
                        if looks_silent(first_block):
                            log.error(
                                "mic returned silence — check microphone permission "
                                "for the controlling app (macOS Tahoe returns zeros "
                                "when access is denied without an error)"
                            )

            cap._on_frame = _on_frame_silence_check

        # 5. Create job and register as current session.
        job = Job(frames=frames, stop=stop)
        with self._lock:
            self._current = (stop, job)

        try:
            # 6. Submit job to inference thread.
            self._jobs.put(job)

            # 7. Drain job.out, writing each event to the socket.
            from .protocol import encode_partial, encode_final, encode_done

            while True:
                try:
                    item = job.out.get(timeout=30.0)
                except queue.Empty:
                    log.warning("dictate: timed out waiting for inference result")
                    break

                if item[0] == "partial":
                    _, text, volatile = item
                    line_out = encode_partial(text, volatile)
                    try:
                        f.write(line_out.encode())
                        f.flush()
                    except OSError:
                        break
                elif item[0] == "final":
                    _, text = item
                    try:
                        f.write(encode_final(text).encode())
                        f.flush()
                    except OSError:
                        pass
                elif item[0] == "done":
                    try:
                        f.write(encode_done().encode())
                        f.flush()
                    except OSError:
                        pass
                    break

        except Exception as exc:
            log.error("dictate session error: %s", exc)
            try:
                f.write((json.dumps({"ok": False, "error": str(exc)}) + "\n").encode())
                f.flush()
            except OSError:
                pass
        finally:
            if cap is not None:
                try:
                    cap.close()
                except Exception:
                    pass
            # Clear current session if still ours.
            with self._lock:
                if self._current is not None and self._current[0] is stop:
                    self._current = None

    # ------------------------------------------------------------------
    # Accept loop + startup
    # ------------------------------------------------------------------

    def run(self) -> int:
        # Single-instance lock.
        if not self._acquire_lock():
            print("speakwrite daemon: already running", file=sys.stderr)
            return 0

        # Remove stale socket file.
        sp = socket_path()
        try:
            sp.unlink()
        except FileNotFoundError:
            pass

        # Bind and listen BEFORE loading the model so clients can connect
        # and queue right away (they'll wait in accept).
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock = srv
        try:
            srv.bind(str(sp))
        except OSError as exc:
            log.error("bind failed: %s", exc)
            self._release_lock()
            return 1
        srv.listen(16)

        # Start the inference thread.  It loads the model and warms up on its
        # own stack, then signals self._ready.
        self._infer_thread = threading.Thread(
            target=self._infer_loop,
            name="speakwrite-infer",
            daemon=True,
        )
        self._infer_thread.start()

        # Wait until the model is loaded and warmed on the inference thread.
        self._ready.wait()

        log.info("daemon ready")  # orchestrator greps for "daemon ready"
        print("speakwrite daemon: daemon ready", file=sys.stderr)

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
        # Join the inference thread so it can finish any in-flight job.
        if self._infer_thread is not None and self._infer_thread.is_alive():
            self._infer_thread.join(timeout=5.0)

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
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
