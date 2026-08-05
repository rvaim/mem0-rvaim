#!/usr/bin/env python3
"""Compatibility entry point for the official on_pre_compact.py.

Reads hook JSON on stdin and forwards to the daemon's /v1/capture with
source=pre-compact.  Kept so any external hook wiring that calls this
file directly keeps working.

Input:  JSON on stdin (transcript_path, session_id, cwd, agent_id)
Output: optional --status line; exit 0 always
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from service.client import capture  # noqa: E402

log = logging.getLogger("mem0-capture")
log.addHandler(logging.StreamHandler(sys.stderr))
log.setLevel(logging.DEBUG if os.environ.get("MEM0_DEBUG") else logging.INFO)


def main() -> None:
    source = "pre-compaction"
    show_status = False
    for arg in sys.argv[1:]:
        if arg.startswith("--source="):
            source = arg.split("=", 1)[1]
        elif arg == "--status":
            show_status = True

    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        hook_input = {}

    agent_id = hook_input.get("agent_id", "")
    if agent_id:
        log.debug("Subagent session, skipping")
        return

    transcript_path = hook_input.get("transcript_path", "")
    if not transcript_path:
        log.debug("No transcript_path provided")
        return

    session_id = hook_input.get("session_id", "") or os.environ.get("MEM0_SESSION_ID", "")
    if not session_id:
        sid_file = f"/tmp/mem0_session_id_{os.environ.get('USER', 'default')}"
        if os.path.isfile(sid_file):
            try:
                with open(sid_file) as fh:
                    session_id = fh.read().strip()
            except OSError:
                pass
    cwd = hook_input.get("cwd") or os.environ.get("MEM0_CWD") or os.getcwd()

    result = capture(
        session_id=session_id,
        transcript_path=transcript_path,
        cwd=cwd,
        source="pre-compact",
        capture_summary=True,
        timeout=55.0,
    )
    if show_status:
        if result is None:
            print("✨ mem0-rvaim pre-compaction snapshot — daemon unavailable")
        elif result.get("facts", {}).get("captured"):
            print("✨ mem0-rvaim pre-compaction snapshot — saved locally")
        else:
            print("✨ mem0-rvaim pre-compaction snapshot — nothing new to capture")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("Unexpected error: %s", exc)
    sys.exit(0)
