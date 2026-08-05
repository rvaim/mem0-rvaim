#!/usr/bin/env python3
"""Auto-capture recent conversation exchanges via the local daemon.

Compatibility client for the official ``auto_capture.py`` entry point.
It no longer runs on a message-count schedule (removed from the hooks);
callers (scripts, tests) invoke it directly.

Input:  argv[1] = transcript_path (optional)
        env: MEM0_SESSION_ID, MEM0_CWD
Output: stderr logs only (exit 0 always)
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from service.client import capture  # noqa: E402

log = logging.getLogger("mem0-auto-capture")
log.addHandler(logging.StreamHandler(sys.stderr))
log.setLevel(logging.DEBUG if os.environ.get("MEM0_DEBUG") else logging.INFO)


def main() -> None:
    if len(sys.argv) < 2:
        log.debug("No transcript_path argument")
        return
    transcript_path = sys.argv[1]
    if not transcript_path or not os.path.isfile(transcript_path):
        log.debug("Transcript not found: %s", transcript_path)
        return

    session_id = os.environ.get("MEM0_SESSION_ID", "")
    if not session_id:
        sid_file = f"/tmp/mem0_session_id_{os.environ.get('USER', 'default')}"
        if os.path.isfile(sid_file):
            try:
                with open(sid_file) as fh:
                    session_id = fh.read().strip()
            except OSError:
                pass
    cwd = os.environ.get("MEM0_CWD") or os.getcwd()

    result = capture(
        session_id=session_id,
        transcript_path=transcript_path,
        cwd=cwd,
        source="auto_capture",
        capture_summary=False,
        timeout=50.0,
    )
    if result is None:
        log.debug("daemon unavailable, capture skipped")
    elif result.get("facts", {}).get("captured"):
        log.info("Auto-captured %s facts", result["facts"].get("facts", 0))
    else:
        log.debug("capture skipped: %s", result.get("facts", {}).get("reason", "?"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("Unexpected error: %s", exc)
    sys.exit(0)
