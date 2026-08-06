"""Daemon process bootstrap: single-instance lock, spawn, health probe.

Used by the MCP proxy and every hook that needs the daemon.  Guarantees:
    * at most one daemon per data root (file lock + PID probe)
    * a dead/stale daemon is replaced after one safe restart attempt
    * runtime files (pid/port/token) are (re)written by the daemon itself
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional

from . import config as app_config
from . import security

log = logging.getLogger("mem0-rvaim.bootstrap")

DAEMON_READY_TIMEOUT = 90.0  # first run installs/imports nothing; generous
DAEMON_POLL = 0.5


def port_file() -> Path:
    return app_config.root_dir() / "runtime" / "daemon.port"


def pid_file() -> Path:
    return app_config.root_dir() / "runtime" / "daemon.pid"


def lock_dir() -> Path:
    return app_config.root_dir() / "runtime" / "daemon.lock"


def _read_port() -> int:
    try:
        return int(port_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _read_pid() -> int:
    try:
        return int(pid_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is not supported on Windows; probe via OpenProcess
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_lock() -> Optional[object]:
    """Take an OS-level exclusive lock on runtime/daemon.lock.

    msvcrt (Windows) / fcntl (POSIX) file locks are released by the OS
    when the holding process exits — no stale-lock recovery is needed and
    there is no mkdir/create race, so exactly one process can ever hold
    it.  Returns the open handle (must be passed to _release_lock) or
    None when the lock is held elsewhere.
    """
    lock = lock_dir()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock, "a+b")
    except OSError:
        return None
    try:
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def _release_lock(lock: Optional[object]) -> None:
    if lock is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        lock.close()
    except OSError:
        pass


def base_url() -> str:
    port = _read_port()
    return f"http://127.0.0.1:{port}" if port else ""


def request(
    method: str,
    path: str,
    payload: Optional[Dict] = None,
    timeout: float = 10.0,
    token: Optional[str] = None,
) -> Dict:
    """Call the daemon HTTP API. Raises on connection failure."""
    port = _read_port()
    if not port:
        raise ConnectionError("daemon port file missing")
    tok = token if token is not None else security.read_token()
    url = f"http://127.0.0.1:{port}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ConnectionError(f"daemon HTTP {exc.code}: {raw[:200]}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ConnectionError(f"daemon unreachable: {exc}") from exc


def is_healthy(token: Optional[str] = None, timeout: float = 3.0) -> bool:
    port = _read_port()
    if not port:
        return False
    try:
        result = request("GET", "/health", timeout=timeout, token=token)
        return bool(result.get("ok"))
    except (ConnectionError, OSError, json.JSONDecodeError):
        return False


def _spawn_daemon(data_dir: Path) -> subprocess.Popen:
    """Start the daemon as a detached background process.

    Launched as a package module (`python -m service.daemon`) with the
    plugin root on sys.path so relative imports always resolve regardless
    of the invoking cwd.
    """
    python = sys.executable
    if os.name == "nt":
        # pythonw.exe is a GUI-subsystem binary: no console window ever
        # appears when the daemon is spawned from a windowless parent
        # (hooks run under pythonw, Claude Code itself may be windowless).
        alt = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.isfile(alt):
            python = alt
    plugin_root = Path(__file__).resolve().parent.parent
    cmd = [python, "-m", "service.daemon", "--data-dir", str(data_dir)]
    env = dict(os.environ)
    # local-first: never phone home to posthog; users can opt back in
    env.setdefault("MEM0_TELEMETRY", "false")

    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        proc = subprocess.Popen(
            cmd, env=env, cwd=str(plugin_root), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags, close_fds=True,
        )
    else:
        proc = subprocess.Popen(
            cmd, env=env, cwd=str(plugin_root), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    return proc


def ensure_daemon(
    data_dir: Optional[Path] = None,
    token: Optional[str] = None,
    timeout: float = DAEMON_READY_TIMEOUT,
) -> Dict:
    """Make sure a healthy daemon is running; returns its connection info.

    One safe restart is attempted if a stale PID / unreachable daemon is
    detected.  Never raises on failure — callers degrade gracefully.
    """
    data_dir = data_dir or (app_config.ensure_root() / "data")

    # fast path: healthy daemon already running
    if is_healthy(token, timeout=2.0):
        return {"ok": True, "started": False, "port": _read_port()}

    pid = _read_pid()
    if pid and _pid_alive(pid) and is_healthy(token, timeout=2.0):
        return {"ok": True, "started": False, "port": _read_port()}

    if pid and _pid_alive(pid):
        # alive but not responding: stale token file (daemon regenerates on start)
        log.warning("daemon pid %d alive but unhealthy; restarting", pid)

    lock = _acquire_lock()
    if lock is None:
        # Another process holds the spawn lock.  Wait for the daemon it is
        # starting to become healthy for as long as we would have waited
        # ourselves (a cold start imports mem0/qdrant and can take 20-30s;
        # a shorter fixed wait here made waiting callers give up and spawn
        # a second instance after the lock was released).
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_healthy(token, timeout=2.0):
                return {"ok": True, "started": False, "port": _read_port()}
            time.sleep(1.0)
        return {"ok": False, "started": False, "error": "daemon-lock-busy"}

    try:
        # double check under lock
        if is_healthy(token, timeout=2.0):
            return {"ok": True, "started": False, "port": _read_port()}

        # kill a zombie pid before spawning (best effort)
        if pid and _pid_alive(pid):
            try:
                os.kill(pid, 9 if os.name != "nt" else 15)
                time.sleep(0.5)
            except OSError:
                pass

        log.info("spawning daemon (data_dir=%s)", data_dir)
        proc = _spawn_daemon(data_dir)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_healthy(token, timeout=2.0):
                log.info("daemon ready on port %d", _read_port())
                return {"ok": True, "started": True, "port": _read_port()}
            time.sleep(DAEMON_POLL)
        # safe restart: kill and try once more
        try:
            if proc.poll() is None:
                proc.terminate()
                time.sleep(1.0)
        except OSError:
            pass
        log.warning("daemon did not become healthy; retrying once")
        proc2 = _spawn_daemon(data_dir)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_healthy(token, timeout=2.0):
                return {"ok": True, "started": True, "port": _read_port(), "restarted": True}
            time.sleep(DAEMON_POLL)
        log.error("daemon failed to start twice")
        return {"ok": False, "started": False, "error": "daemon-start-failed"}
    finally:
        _release_lock(lock)


def stop_daemon() -> bool:
    """Stop the running daemon (used by admin scripts)."""
    pid = _read_pid()
    if not pid or not _pid_alive(pid):
        return False
    try:
        os.kill(pid, 15)
        return True
    except OSError:
        return False


def local_socket_available(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return False
    except OSError:
        return True
