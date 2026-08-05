#!/usr/bin/env python3
"""Capture a structured session summary via the local daemon.

Compatibility client for the official ``capture_session_summary.py``:
reads hook JSON on stdin (transcript_path, session_id, cwd, agent_id),
then asks the daemon to generate and store the summary.  The summary LLM
is the daemon's own configured model — never the host agent.

Input:  JSON on stdin
Output: stderr logs only (exit 0 always)
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from service.client import capture  # noqa: E402

log = logging.getLogger("mem0-session-summary")
log.addHandler(logging.StreamHandler(sys.stderr))
log.setLevel(logging.DEBUG if os.environ.get("MEM0_DEBUG") else logging.INFO)


def main() -> None:
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
        source="stop",
        capture_summary=True,
        timeout=55.0,
    )
    if result is None:
        log.debug("daemon unavailable, summary skipped")
    elif result.get("summary", {}).get("captured"):
        log.info("Session summary stored (%s chars)",
                 result["summary"].get("chars", "?"))
    else:
        log.debug("summary skipped: %s", result.get("summary", {}).get("reason", "?"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("Unexpected error: %s", exc)
    sys.exit(0)
