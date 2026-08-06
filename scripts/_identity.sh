# Source this file. Sets MEM0_RESOLVED_USER_ID, MEM0_WORKSPACE_ID, and settings.
#
# Local version: no cloud API key anywhere. The user id is the OS user name
# (or MEM0_USER_ID override); the daemon derives its own stable identity.

_SCRIPT_DIR="$( cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd )"
. "$_SCRIPT_DIR/_py.sh" 2>/dev/null || true

_mem0_resolve_identity() {
  if [ -n "${MEM0_USER_ID:-}" ]; then
    printf '%s' "$MEM0_USER_ID"
    return
  fi
  printf '%s' "${USER:-${USERNAME:-default}}"
}

MEM0_RESOLVED_USER_ID="$(_mem0_resolve_identity)"
export MEM0_RESOLVED_USER_ID

_MEM0_IDENTITY_ANNOTATION=""
if [ -n "${MEM0_USER_ID:-}" ] && [ "$MEM0_USER_ID" != "${USER:-default}" ]; then
  _MEM0_IDENTITY_ANNOTATION=" (override; default: ${USER:-default})"
fi
export _MEM0_IDENTITY_ANNOTATION

# Load settings from ~/.mem0/settings.json (or daemon config).
# Python is resolved by _py.sh: venv python first, then system `python`.
# Never call `python3` directly — on Windows it is a no-op Store stub.
_MEM0_PY_BIN="$(_mem0_python 2>/dev/null)"
if [ -n "$_MEM0_PY_BIN" ]; then
  _SETTINGS_JSON=$(PYTHONPATH="$_SCRIPT_DIR" "$_MEM0_PY_BIN" -c "from load_settings import load_settings; import json; print(json.dumps(load_settings()))" 2>/dev/null || echo "{}")
  MEM0_AUTO_SAVE=$(printf '%s' "$_SETTINGS_JSON" | "$_MEM0_PY_BIN" -c "import sys,json; print(str(json.load(sys.stdin).get('auto_save',True)).lower())" 2>/dev/null || echo "true")
  MEM0_AUTO_SEARCH=$(printf '%s' "$_SETTINGS_JSON" | "$_MEM0_PY_BIN" -c "import sys,json; print(str(json.load(sys.stdin).get('auto_search',True)).lower())" 2>/dev/null || echo "true")
  MEM0_SEARCH_LIMIT=$(printf '%s' "$_SETTINGS_JSON" | "$_MEM0_PY_BIN" -c "import sys,json; print(json.load(sys.stdin).get('search_limit',10))" 2>/dev/null || echo "10")
  MEM0_CONFIDENCE_THRESHOLD=$(printf '%s' "$_SETTINGS_JSON" | "$_MEM0_PY_BIN" -c "import sys,json; print(json.load(sys.stdin).get('confidence_threshold',0.3))" 2>/dev/null || echo "0.3")
  MEM0_DEBUG=$(printf '%s' "$_SETTINGS_JSON" | "$_MEM0_PY_BIN" -c "import sys,json; print(str(json.load(sys.stdin).get('debug',False)).lower())" 2>/dev/null || echo "false")
else
  MEM0_AUTO_SAVE="true"; MEM0_AUTO_SEARCH="true"; MEM0_SEARCH_LIMIT="10"
  MEM0_CONFIDENCE_THRESHOLD="0.3"; MEM0_DEBUG="false"
fi
export MEM0_AUTO_SAVE MEM0_AUTO_SEARCH MEM0_SEARCH_LIMIT MEM0_CONFIDENCE_THRESHOLD MEM0_DEBUG

# Resolve workspace context
. "$_SCRIPT_DIR/_project.sh"
