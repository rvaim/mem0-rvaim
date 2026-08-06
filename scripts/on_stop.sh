#!/usr/bin/env bash
# Hook: Stop
#
# Captures the completed turn: facts + optional session summary, via the
# local daemon /v1/capture.  Skips subagent sessions.  Runs in the
# background; always exits 0.
set -uo pipefail

if [ -n "${MEM0_DEBUG:-}" ]; then
  mkdir -p "$HOME/.mem0" && exec 2>>"$HOME/.mem0/hooks.log"
fi

INPUT=$(cat)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_py.sh" 2>/dev/null || true

AGENT_ID=$(printf '%s' "$INPUT" | _mem0_jq '.agent_id' "")
if [ -n "$AGENT_ID" ]; then
  exit 0  # subagent session
fi

CWD=$(printf '%s' "$INPUT" | _mem0_jq '.cwd' "")
if [ -z "$CWD" ]; then
  CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
export MEM0_HOOK_CWD="$CWD"
export MEM0_CWD="$CWD"

. "$SCRIPT_DIR/_identity.sh" 2>/dev/null || true
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "${MEM0_AUTO_SAVE:-true}" = "false" ]; then
  exit 0
fi

TRANSCRIPT_PATH=$(printf '%s' "$INPUT" | _mem0_jq '.transcript_path' "")
if [ -z "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

SESSION_ID=$(printf '%s' "$INPUT" | _mem0_jq '.session_id' "")
if [ -z "$SESSION_ID" ]; then
  _SID_FILE="/tmp/mem0_session_id_${USER:-default}"
  [ -f "$_SID_FILE" ] && SESSION_ID=$(cat "$_SID_FILE" 2>/dev/null) || true
fi

# Background capture via the daemon (never blocks the turn end).
# Paths go through env vars — never interpolated into python source.
(
  PY_BIN="$(_mem0_python 2>/dev/null)"
  [ -n "$PY_BIN" ] || PY_BIN="python"
  MEM0_SESSION_ID="$SESSION_ID" MEM0_CWD="$CWD" MEM0_TRANSCRIPT_PATH="$TRANSCRIPT_PATH" \
  MEM0_PLUGIN_ROOT="$PLUGIN_ROOT" \
    "$PY_BIN" -c "
import os, sys
sys.path.insert(0, os.environ.get('MEM0_PLUGIN_ROOT', ''))
from service.client import capture
capture(
    session_id=os.environ.get('MEM0_SESSION_ID', ''),
    transcript_path=os.environ.get('MEM0_TRANSCRIPT_PATH', ''),
    cwd=os.environ.get('MEM0_CWD', ''),
    source='stop',
    capture_summary=True,
    timeout=55.0,
)
" >/dev/null 2>&1
) &

exit 0
