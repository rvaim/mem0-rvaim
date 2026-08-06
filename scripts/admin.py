#!/usr/bin/env python3
"""mem0-rvaim admin CLI (local).

Usage:
    python admin.py status          show daemon health + stats
    python admin.py start           ensure daemon is running
    python admin.py stop            stop the daemon
    python admin.py restart         restart the daemon
    python admin.py config          print effective config (secrets redacted)
    python admin.py export [scope]  export memories to stdout (markdown)
    python admin.py import <file> [scope]   import an export file
    python admin.py wipe [scope]    delete memories (global | workspace)
    python admin.py doctor          dependency + daemon diagnostics

All data stays on this machine under ~/.mem0/local.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from service import bootstrap, config as app_config, security  # noqa: E402

REDACTED = "[REDACTED]"


def _redact(cfg: dict) -> dict:
    out = json.loads(json.dumps(cfg))
    for section in ("llm", "summary_llm", "recall_llm", "embedder"):
        if isinstance(out.get(section), dict) and out[section].get("api_key"):
            out[section]["api_key"] = REDACTED
    return out


def cmd_status() -> int:
    health = bootstrap.request("GET", "/health", timeout=3.0) if bootstrap.is_healthy(timeout=2.0) else None
    print(f"daemon: {'running' if health else 'not running'}")
    if health:
        print(f"version: {health.get('version')}  pid: {health.get('pid')}  degraded: {health.get('degraded')}")
        try:
            stats = bootstrap.request("GET", "/v1/stats", timeout=5.0)
            print(f"memories: {stats.get('memory_counts')}  sessions: {stats.get('sessions_known')}")
            print(f"workspace: {stats.get('workspace_id')}  retries: {stats.get('pending_retries')}")
        except Exception:
            pass
    return 0


def cmd_start() -> int:
    result = bootstrap.ensure_daemon()
    print(f"daemon started: {result.get('ok')} (port={result.get('port')}, started={result.get('started', False)})")
    return 0 if result.get("ok") else 1


def cmd_stop() -> int:
    ok = bootstrap.stop_daemon()
    print("daemon stopped" if ok else "daemon was not running")
    return 0


def cmd_restart() -> int:
    bootstrap.stop_daemon()
    return cmd_start()


def cmd_config() -> int:
    cfg = app_config.load_config()
    print(json.dumps(_redact(cfg), indent=2, ensure_ascii=False))
    return 0


def cmd_export(scope: str = "both") -> int:
    if not ensure_daemon():
        print("daemon not available", file=sys.stderr)
        return 1
    session = os.environ.get("MEM0_SESSION_ID", "") or "admin-session"
    result = bootstrap.request("POST", "/v1/export",
                               {"session_id": session, "scope": scope, "format": "markdown"},
                               timeout=60.0)
    print(result.get("content", ""))
    return 0


def cmd_import(path: str, scope: str = "workspace") -> int:
    if not ensure_daemon():
        print("daemon not available", file=sys.stderr)
        return 1
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 1
    session = os.environ.get("MEM0_SESSION_ID", "") or "admin-session"
    result = bootstrap.request("POST", "/v1/import",
                               {"session_id": session, "content": content, "scope": scope},
                               timeout=120.0)
    print(f"imported {result.get('imported', 0)} memories")
    return 0


def cmd_wipe(scope: str = "workspace") -> int:
    if not ensure_daemon():
        print("daemon not available", file=sys.stderr)
        return 1
    if scope not in ("global", "workspace"):
        print("scope must be global or workspace", file=sys.stderr)
        return 1
    session = os.environ.get("MEM0_SESSION_ID", "") or "admin-session"
    result = bootstrap.request("POST", "/v1/memories/delete_all",
                               {"session_id": session, "scope": scope}, timeout=60.0)
    print(f"deleted {result.get('deleted', 0)} memories (scope={scope})")
    return 0


def cmd_doctor() -> int:
    print(f"data root: {app_config.root_dir()}")
    print(f"venv: {app_config.root_dir() / 'venv'}")
    print(f"daemon healthy: {bootstrap.is_healthy(timeout=2.0)}")
    cfg = app_config.load_config()
    llm = cfg.get("llm") or {}
    emb = cfg.get("embedder") or {}
    print(f"memory llm: {llm.get('provider')}/{llm.get('model')} @ {llm.get('base_url')}")
    print(f"embedder:   {emb.get('provider')}/{emb.get('model')} @ {emb.get('base_url')}")
    try:
        import mem0  # noqa: F401
        print("mem0 SDK: installed")
    except ImportError:
        print("mem0 SDK: NOT installed (run ensure_deps.sh)")
    try:
        import qdrant_client  # noqa: F401
        print("qdrant-client: installed")
    except ImportError:
        print("qdrant-client: NOT installed")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "status":
        return cmd_status()
    if cmd == "start":
        return cmd_start()
    if cmd == "stop":
        return cmd_stop()
    if cmd == "restart":
        return cmd_restart()
    if cmd == "config":
        return cmd_config()
    if cmd == "export":
        return cmd_export(args[1] if len(args) > 1 else "both")
    if cmd == "import":
        if len(args) < 2:
            print("usage: admin.py import <file> [scope]", file=sys.stderr)
            return 1
        return cmd_import(args[1], args[2] if len(args) > 2 else "workspace")
    if cmd == "wipe":
        return cmd_wipe(args[1] if len(args) > 1 else "workspace")
    if cmd == "doctor":
        return cmd_doctor()
    print(f"unknown command: {cmd}", file=sys.stderr)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
