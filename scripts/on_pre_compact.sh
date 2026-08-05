#!/usr/bin/env bash
# Hook: PreCompact
#
# Forces a capture of unprocessed transcript content BEFORE compaction so
# no context is lost.  Background, silent, always exits 0.
set -uo pipefail

if [ -n "${MEM0_DEBUG:-}" ]; then
  mkdir -p "$HOME/.mem0" && exec 2>>"$HOME/.mem0/hooks.log"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_identity.sh" 2>/dev/null || true
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INPUT=$(cat)

AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // ""' 2>/dev/null || echo "")
if [ -n "$AGENT_ID" ]; then
  exit 0
fi

TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // ""' 2>/dev/null || echo "")
if [ -z "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""' 2>/dev/null || echo "")
if [ -z "$SESSION_ID" ]; then
  _SID_FILE="/tmp/mem0_session_id_${USER:-default}"
  [ -f "$_SID_FILE" ] && SESSION_ID=$(cat "$_SID_FILE" 2>/dev/null) || true
fi
CWD=$(echo "$INPUT" | jq -r '.cwd // "'"$PWD"'"' 2>/dev/null || echo "$PWD")

_TMP="/tmp/mem0_precompact_input_$$.json"
printf '%s' "$INPUT" > "$_TMP" 2>/dev/null

(
  VENV_PY="${CLAUDE_PLUGIN_DATA:-$HOME/.mem0/local}/venv/bin/python3"
  [ -x "$VENV_PY" ] || VENV_PY="${CLAUDE_PLUGIN_DATA:-$HOME/.mem0/local}/venv/Scripts/python.exe"
  PY_BIN="python3"
  if [ -n "${VENV_PY:-}" ] && [ -x "${VENV_PY:-/nonexistent}" ]; then
    PY_BIN="$VENV_PY"
  fi
  MEM0_CWD="$CWD" MEM0_SESSION_ID="$SESSION_ID" PYTHONPATH="$PLUGIN_ROOT" \
    "$PY_BIN" -c "
import os, sys
sys.path.insert(0, '$PLUGIN_ROOT')
from service.client import capture
capture(
    session_id=os.environ.get('MEM0_SESSION_ID', ''),
    transcript_path='$TRANSCRIPT_PATH',
    cwd=os.environ.get('MEM0_CWD', ''),
    source='pre-compact',
    capture_summary=True,
    timeout=55.0,
)
" >/dev/null 2>&1
  rm -f "$_TMP"
) &

exit 0
