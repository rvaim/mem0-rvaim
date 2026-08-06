#!/usr/bin/env python3
"""Windows hook entry: runs all hook logic under pythonw.exe (no console).

On Windows, Claude Code spawns hook commands from a windowless parent;
spawning bash.exe / python.exe (console-subsystem programs) makes Windows
open a black console window for each one, and a hung daemon leaves them
open.  pythonw.exe is a GUI-subsystem binary — it never creates a window —
yet it still inherits the stdin/stdout pipes Claude Code provides, so
JSON in/out works exactly as with a console program.

Pure standard library: talks to the daemon over loopback HTTP via
service.client (which never imports mem0 / qdrant), so any system python
can run this.  All paths come from env vars / argv, never interpolated.

Usage: pythonw hook_runner.py --hook <session-start|user-prompt|stop|pre-compact|block-write|ensure-deps>
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)
SCRIPT_DIR = str(Path(__file__).resolve().parent)
sys.path.insert(0, PLUGIN_ROOT)
sys.path.insert(0, SCRIPT_DIR)

_UTF8_KW = {"encoding": "utf-8", "errors": "replace"}


def _setup_stdio() -> None:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(**_UTF8_KW)
        except Exception:
            pass


def _read_input() -> dict:
    raw = b""
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        raw = sys.stdin.read().encode("utf-8", errors="replace")
    try:
        return json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return {}


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def _cwd(payload: dict) -> str:
    cwd = (payload.get("cwd") or "").strip()
    if not cwd:
        cwd = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if not cwd:
        cwd = os.getcwd()
    return cwd


def _user() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or "default"


def _session_id(payload: dict) -> str:
    sid = (payload.get("session_id") or "").strip()
    if not sid:
        sid = os.environ.get("MEM0_SESSION_ID", "").strip()
    if not sid:
        sid = f"ses_{os.getpid()}_{int(__import__('time').time())}"
    return sid


def _write_session_file(sid: str) -> None:
    try:
        path = os.path.join(tempfile.gettempdir(), f"mem0_session_id_{_user()}")
        Path(path).write_text(sid, encoding="utf-8")
    except OSError:
        pass


def _workspace(cwd: str) -> tuple[str, str]:
    """workspace_id + branch via scripts/_project (pure stdlib)."""
    try:
        from _project import resolve_branch, resolve_workspace_id

        return resolve_workspace_id(cwd), resolve_branch(cwd)
    except Exception:
        return "unknown", "unknown"


def _daemon_healthy() -> bool:
    try:
        from service import bootstrap

        return bootstrap.is_healthy(timeout=1.5)
    except Exception:
        return False


def _ensure_daemon(timeout: float = 90.0) -> bool:
    try:
        from service import bootstrap

        result = bootstrap.ensure_daemon(timeout=timeout)
        return bool(result.get("ok"))
    except Exception:
        return False


# ----------------------------------------------------------------------
def hook_session_start(payload: dict) -> int:
    from service.client import register_session

    cwd = _cwd(payload)
    workspace_id, branch = _workspace(cwd)
    sid = _session_id(payload)
    _write_session_file(sid)

    _ensure_daemon()
    register_session(
        session_id=sid, cwd=cwd, workspace_id=workspace_id, host="claude"
    )

    uid = os.environ.get("MEM0_USER_ID") or _user()
    print(
        "## Mem0 Active (local)\n"
        f"\n`user={uid} | workspace={workspace_id} | branch={branch} | mode=local`\n"
        "\nMemory is stored locally by the mem0-rvaim daemon. Workspace-scoped\n"
        "memories are isolated per project; use the `mem0:remember` skill to\n"
        "store explicit facts and `mem0:peek` to search.\n"
    )
    return 0


def hook_user_prompt(payload: dict) -> int:
    from service.client import recall

    prompt = (payload.get("prompt") or "").strip()
    if len(prompt) < 20:
        return 0  # short replies don't warrant memory context

    auto_search = os.environ.get("MEM0_AUTO_SEARCH", "true")
    if auto_search.lower() == "false":
        return 0

    if not _daemon_healthy():
        return 0  # never block the prompt waiting for the daemon

    sid = _session_id(payload)
    result = recall(prompt, session_id=sid, timeout=2.5)
    context = (result or {}).get("context") or ""
    if context:
        _emit(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                }
            }
        )
    return 0


def hook_capture(payload: dict, source: str) -> int:
    from service.client import capture

    transcript = (payload.get("transcript_path") or "").strip()
    if not transcript:
        return 0
    if payload.get("agent_id"):
        return 0  # subagent session

    sid = _session_id(payload)
    _ensure_daemon()
    capture(
        session_id=sid,
        transcript_path=transcript,
        cwd=_cwd(payload),
        source=source,
        capture_summary=True,
        timeout=50.0,
    )
    return 0


def hook_block_write(payload: dict) -> int:
    import fnmatch

    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if not path:
        return 0
    p = path.replace("\\", "/")
    blocked = (
        fnmatch.fnmatch(p, "*/.claude/*/MEMORY.md")
        or fnmatch.fnmatch(p, "*/.claude/memory/*")
        or fnmatch.fnmatch(p, "*/.mem0/local/data/*")
    )
    if blocked:
        sys.stderr.write(
            "BLOCKED: Do not write to %s. Use the mem0-rvaim `add_memory` MCP "
            "tool instead to persist memories (or `mem0:remember`). The local "
            "memory daemon owns all memory storage.\n" % path
        )
        sys.stderr.flush()
        return 2
    return 0


def hook_ensure_deps() -> int:
    import shutil
    import subprocess

    from service import bootstrap

    data_dir = Path(
        os.environ.get("CLAUDE_PLUGIN_DATA")
        or os.path.join(os.path.expanduser("~"), ".mem0", "local")
    )
    venv_dir = data_dir / "venv"
    req_src = Path(PLUGIN_ROOT) / "requirements.txt"
    stamp = data_dir / "requirements.txt"

    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def venv_python() -> Path | None:
        for cand in (venv_dir / "Scripts" / "python.exe", venv_dir / "bin" / "python3", venv_dir / "bin" / "python"):
            if cand.is_file():
                return cand
        return None

    needs = False
    venv_py = venv_python()
    if venv_py is None:
        needs = True
    elif stamp.is_file() and req_src.is_file():
        try:
            if stamp.read_bytes() != req_src.read_bytes():
                needs = True
        except OSError:
            needs = True
    else:
        needs = True

    if needs:
        data_dir.mkdir(parents=True, exist_ok=True)
        lock = data_dir / ".install-lock"
        try:
            lock.mkdir()
        except OSError:
            return 0  # another process is installing
        try:
            if venv_py is None:
                base = shutil.which("python") or "python"
                subprocess.run(
                    [base, "-m", "venv", str(venv_dir)],
                    creationflags=flags, timeout=180,
                )
                venv_py = venv_python()
            if venv_py is not None and req_src.is_file():
                subprocess.run(
                    [str(venv_py), "-m", "pip", "install", "--quiet", "-r", str(req_src)],
                    creationflags=flags, timeout=600,
                )
                try:
                    stamp.write_bytes(req_src.read_bytes())
                except OSError:
                    pass
        finally:
            try:
                lock.rmdir()
            except OSError:
                pass

    bootstrap.ensure_daemon(timeout=90.0)
    return 0


# ----------------------------------------------------------------------
def main() -> int:
    _setup_stdio()
    args = sys.argv[1:]
    hook = ""
    if "--hook" in args:
        hook = args[args.index("--hook") + 1]
    payload = _read_input()

    handlers = {
        "session-start": lambda: hook_session_start(payload),
        "user-prompt": lambda: hook_user_prompt(payload),
        "stop": lambda: hook_capture(payload, "stop"),
        "pre-compact": lambda: hook_capture(payload, "pre-compact"),
        "block-write": lambda: hook_block_write(payload),
        "ensure-deps": lambda: hook_ensure_deps(),
    }
    handler = handlers.get(hook)
    if handler is None:
        return 0
    try:
        return handler()
    except Exception:
        return 0  # hooks must never break the host agent


if __name__ == "__main__":
    sys.exit(main())
