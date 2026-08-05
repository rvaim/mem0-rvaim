#!/usr/bin/env bash
# Hook: UserPromptSubmit
#
# Local recall: calls the daemon /v1/recall with the current user prompt
# and injects the top relevant memories as additionalContext.  No rubric
# telling the agent to search/save on its own; the daemon does all query
# generation.  Must never block the user's prompt (2s budget here).
set -uo pipefail

if [ -n "${MEM0_DEBUG:-}" ]; then
  mkdir -p "$HOME/.mem0" && exec 2>>"$HOME/.mem0/hooks.log"
fi

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // ""' 2>/dev/null || echo "")

# Acknowledgements and short replies don't warrant memory context
if [ ${#PROMPT} -lt 20 ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_identity.sh" 2>/dev/null || true
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "${MEM0_AUTO_SEARCH:-true}" = "false" ]; then
  exit 0
fi

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""' 2>/dev/null || echo "")
if [ -z "$SESSION_ID" ]; then
  _SID_FILE="/tmp/mem0_session_id_${USER:-default}"
  [ -f "$_SID_FILE" ] && SESSION_ID=$(cat "$_SID_FILE" 2>/dev/null) || true
fi
if [ -z "$SESSION_ID" ]; then
  SESSION_ID="default_${USER:-unknown}"
fi

# Call the daemon recall endpoint. Use the venv python if present.
VENV_PY="${CLAUDE_PLUGIN_DATA:-$HOME/.mem0/local}/venv/bin/python3"
[ -x "$VENV_PY" ] || VENV_PY="${CLAUDE_PLUGIN_DATA:-$HOME/.mem0/local}/venv/Scripts/python.exe"
PY_BIN="python3"
if [ -n "${VENV_PY:-}" ] && [ -x "${VENV_PY:-/nonexistent}" ]; then
  PY_BIN="$VENV_PY"
fi

RESULT=$(printf '%s' "$PROMPT" | MEM0_CWD="$MEM0_CWD_RESOLVED" MEM0_SESSION_ID="$SESSION_ID" \
  PYTHONPATH="$PLUGIN_ROOT" "$PY_BIN" -c "
import json, os, sys
sys.path.insert(0, '$PLUGIN_ROOT')
from service.client import recall
query = sys.stdin.read().strip()
result = recall(query, session_id=os.environ.get('MEM0_SESSION_ID', ''), timeout=2.5)
if result and result.get('context'):
    print(json.dumps(result['context']))
" 2>/dev/null || echo "")

if [ -n "$RESULT" ]; then
  _CTX=$(printf '%s' "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin))" 2>/dev/null || echo "$RESULT")
  jq -cn --arg ctx "$_CTX" '{
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: $ctx
    }
  }' 2>/dev/null || true
fi

exit 0
