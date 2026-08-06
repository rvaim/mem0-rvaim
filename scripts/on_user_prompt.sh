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
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_py.sh" 2>/dev/null || true

PROMPT=$(printf '%s' "$INPUT" | _mem0_jq '.prompt' "")

# Acknowledgements and short replies don't warrant memory context
if [ ${#PROMPT} -lt 20 ]; then
  exit 0
fi

CWD=$(printf '%s' "$INPUT" | _mem0_jq '.cwd' "")
if [ -z "$CWD" ]; then
  CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
export MEM0_CWD="$CWD"
export MEM0_HOOK_CWD="$CWD"

. "$SCRIPT_DIR/_identity.sh" 2>/dev/null || true
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "${MEM0_AUTO_SEARCH:-true}" = "false" ]; then
  exit 0
fi

SESSION_ID=$(printf '%s' "$INPUT" | _mem0_jq '.session_id' "")
if [ -z "$SESSION_ID" ]; then
  _SID_FILE="/tmp/mem0_session_id_${USER:-default}"
  [ -f "$_SID_FILE" ] && SESSION_ID=$(cat "$_SID_FILE" 2>/dev/null) || true
fi
if [ -z "$SESSION_ID" ]; then
  SESSION_ID="default_${USER:-unknown}"
fi

# Call the daemon recall endpoint. Use the venv python if present.
PY_BIN="$(_mem0_python 2>/dev/null)"
[ -n "$PY_BIN" ] || PY_BIN="python"

RESULT=$(printf '%s' "$PROMPT" | MEM0_CWD="$CWD" MEM0_SESSION_ID="$SESSION_ID" \
  MEM0_PLUGIN_ROOT="$PLUGIN_ROOT" "$PY_BIN" -c "
import json, os, sys
sys.path.insert(0, os.environ.get('MEM0_PLUGIN_ROOT', ''))
sys.stdin.reconfigure(encoding='utf-8', errors='replace')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from service.client import recall
query = sys.stdin.read().strip()
result = recall(query, session_id=os.environ.get('MEM0_SESSION_ID', ''), timeout=2.5)
if result and result.get('context'):
    print(json.dumps(result['context']))
" 2>/dev/null || echo "")

if [ -n "$RESULT" ]; then
  _CTX=$(printf '%s' "$RESULT" | "$PY_BIN" -c "
import sys, json
sys.stdin.reconfigure(encoding='utf-8', errors='replace')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
print(json.load(sys.stdin))" 2>/dev/null || echo "$RESULT")
  _mem0_hook_json "$_CTX" 2>/dev/null || true
fi

exit 0
