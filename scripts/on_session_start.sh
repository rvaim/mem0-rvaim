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
. "$SCRIPT_DIR/_py.sh" 2>/dev/null || true
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INPUT=$(cat)
SOURCE=$(printf '%s' "$INPUT" | _mem0_jq '.source' "startup")

MEM0_CWD_RESOLVED=$(printf '%s' "$INPUT" | _mem0_jq '.cwd' "")
if [ -z "$MEM0_CWD_RESOLVED" ]; then
  # SessionStart payload has no cwd; fall back to the project dir Claude
  # Code exports on the hook process, then the hook's own working dir.
  MEM0_CWD_RESOLVED="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
export MEM0_CWD="$MEM0_CWD_RESOLVED"
export MEM0_HOOK_CWD="$MEM0_CWD_RESOLVED"

. "$SCRIPT_DIR/_identity.sh" 2>/dev/null || true

MEM0_SESSION_ID=$(printf '%s' "$INPUT" | _mem0_jq '.session_id' "")
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

# Register the session (idempotent). Background-safe: fast path when healthy.
_UID="${MEM0_RESOLVED_USER_ID:-${USER:-default}}"
_PID="${MEM0_PROJECT_ID:-unknown}"
_BR="${MEM0_BRANCH:-unknown}"

# Paths are passed via env vars, never interpolated into python source:
# Windows paths contain backslashes (\U, \n, ...) which are python escape
# sequences and would raise SyntaxError.
MEM0_PLUGIN_ROOT="$PLUGIN_ROOT" MEM0_PROJECT_ID="$_PID" \
"$(_mem0_python)" -c "
import json, os, sys
sys.path.insert(0, os.environ.get('MEM0_PLUGIN_ROOT', ''))
from service.client import register_session
try:
    result = register_session(
        session_id=os.environ.get('MEM0_SESSION_ID', ''),
        cwd=os.environ.get('MEM0_HOOK_CWD', ''),
        workspace_id=os.environ.get('MEM0_PROJECT_ID', ''),
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
