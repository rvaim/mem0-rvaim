"""Client helpers used by hooks / scripts (never opens Qdrant or SQLite).

All communication goes through the loopback daemon API.  Every call is
best-effort: hooks must never block or break the host agent, so failures
are logged and swallowed.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Optional

from . import bootstrap

log = logging.getLogger("mem0-rvaim.client")


def ensure_daemon() -> bool:
    try:
        result = bootstrap.ensure_daemon(timeout=60.0)
        return bool(result.get("ok"))
    except Exception as exc:
        log.debug("ensure_daemon failed: %s", exc)
        return False


def register_session(
    session_id: str,
    cwd: str,
    workspace_id: Optional[str] = None,
    host: str = "unknown",
    pid: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not ensure_daemon():
        return None
    try:
        return bootstrap.request("POST", "/v1/session/register", {
            "session_id": session_id,
            "cwd": cwd,
            "workspace_id": workspace_id,
            "host": host,
            "pid": pid,
        }, timeout=10.0)
    except Exception as exc:
        log.debug("register_session failed: %s", exc)
        return None


def recall(query: str, session_id: str, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    if not ensure_daemon():
        return None
    try:
        return bootstrap.request("POST", "/v1/recall", {
            "query": query,
            "session_id": session_id,
        }, timeout=timeout)
    except Exception as exc:
        log.debug("recall failed: %s", exc)
        return None


def capture(session_id: str, transcript_path: str, cwd: str,
            source: str = "stop", capture_summary: bool = True,
            timeout: float = 60.0) -> Optional[Dict[str, Any]]:
    if not ensure_daemon():
        return None
    try:
        return bootstrap.request("POST", "/v1/capture", {
            "session_id": session_id,
            "transcript_path": transcript_path,
            "cwd": cwd,
            "source": source,
            "capture_summary": capture_summary,
        }, timeout=timeout)
    except Exception as exc:
        log.debug("capture failed: %s", exc)
        return None


def health() -> Optional[Dict[str, Any]]:
    try:
        return bootstrap.request("GET", "/health", timeout=3.0)
    except Exception:
        return None


def get_stats() -> Optional[Dict[str, Any]]:
    if not ensure_daemon():
        return None
    try:
        return bootstrap.request("GET", "/v1/stats", timeout=5.0)
    except Exception:
        return None


def session_id_from_env() -> str:
    return (
        os.environ.get("MEM0_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    )


def python_script(module: str, argv: list[str] | None = None) -> None:
    """Re-exec a sibling script with the daemon on sys.path (helper)."""
    argv = argv or sys.argv[1:]
    sys.argv = [module] + argv
