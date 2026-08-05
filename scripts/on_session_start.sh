#!/usr/bin/env bash
# Hook: SessionStart
#
# Ensures dependencies + daemon, registers the session with the daemon
# (session -> workspace mapping), and prints the status line.  Local only:
# no cloud key, no cloud calls.  Never blocks startup.
set -uo pipefail

if [ -n "${MEM0_DEBUG:-}" ]; then
  mkdir -p "$HOME/.mem0" && exec 2>>"$HOME/.mem0/hooks.log"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_identity.sh" 2>/dev/null || true
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INPUT=$(cat)
SOURCE=$(echo "$INPUT" | jq -r '.source // "startup"' 2>/dev/null || echo "startup")

MEM0_SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""' 2>/dev/null || echo "")
if [ -z "$MEM0_SESSION_ID" ]; then
  MEM0_SESSION_ID="ses_$(date +%s)_$$"
fi
printf '%s' "$MEM0_SESSION_ID" > "/tmp/mem0_session_id_${USER:-default}" 2>/dev/null || true
export MEM0_SESSION_ID

# Persist to Claude's env so Bash tool calls, MCP processes and other hooks
# inherit the session id (needed by the MCP proxy for workspace routing).
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export MEM0_SESSION_ID=\"$MEM0_SESSION_ID\"" >> "$CLAUDE_ENV_FILE" 2>/dev/null || true
  echo "export MEM0_WORKSPACE_ID=\"${MEM0_PROJECT_ID:-unknown}\"" >> "$CLAUDE_ENV_FILE" 2>/dev/null || true
fi

MEM0_CWD_RESOLVED=$(echo "$INPUT" | jq -r '.cwd // "."' 2>/dev/null || echo ".")
export MEM0_CWD="$MEM0_CWD_RESOLVED"

# Register the session (idempotent). Background-safe: fast path when healthy.
_UID="${MEM0_RESOLVED_USER_ID:-${USER:-default}}"
_PID="${MEM0_PROJECT_ID:-unknown}"
_BR="${MEM0_BRANCH:-unknown}"

python3 -c "
import json, os, sys
sys.path.insert(0, '$PLUGIN_ROOT')
from service.client import register_session
try:
    result = register_session(
        session_id='$MEM0_SESSION_ID',
        cwd='$MEM0_CWD_RESOLVED',
        workspace_id='$_PID',
        host='claude',
    )
    print(json.dumps(result or {}))
except Exception as e:
    print('{}')
" 2>/dev/null | read -r _REG || true

# Banner: always local-first, no setup step required
cat <<BANNER
## Mem0 Active (local)

\`user=${_UID}${_MEM0_IDENTITY_ANNOTATION:-} | workspace=${_PID} | branch=${_BR} | mode=local\`

Memory is stored locally by the mem0-rvaim daemon. Workspace-scoped
memories are isolated per project; use the \`mem0:remember\` skill to
store explicit facts and \`mem0:peek\` to search.

BANNER

exit 0
