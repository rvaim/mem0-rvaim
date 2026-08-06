# Source this file. Resolves MEM0_PROJECT_ID (workspace) and branch.
# Local only — no cloud calls.

_SCRIPT_DIR="$( cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd )"
. "$_SCRIPT_DIR/_py.sh" 2>/dev/null || true

# Prefer the cwd passed in the hook payload (MEM0_HOOK_CWD) over $PWD,
# which on Windows may be a directory the hook process was started in.
_MEM0_RESOLVE_CWD="${MEM0_HOOK_CWD:-$PWD}"

_MEM0_PY_BIN="$(_mem0_python 2>/dev/null)"
if [ -n "$_MEM0_PY_BIN" ]; then
  # cwd is passed via env var — never interpolated into python source
  # (Windows backslash paths would break string literals).
  _MEM0_PROJECT_JSON=$(MEM0_RESOLVE_CWD="$_MEM0_RESOLVE_CWD" PYTHONPATH="$_SCRIPT_DIR" "$_MEM0_PY_BIN" -c "
import json, os
from _project import resolve_workspace_id, resolve_branch
cwd = os.environ.get('MEM0_RESOLVE_CWD') or ''
print(json.dumps({'workspace_id': resolve_workspace_id(cwd), 'branch': resolve_branch(cwd)}))
" 2>/dev/null || echo '{"workspace_id":"unknown","branch":"unknown"}')
  MEM0_PROJECT_ID=$(printf '%s' "$_MEM0_PROJECT_JSON" | "$_MEM0_PY_BIN" -c "import sys,json; print(json.load(sys.stdin).get('workspace_id','unknown'))" 2>/dev/null || echo "unknown")
  MEM0_BRANCH=$(printf '%s' "$_MEM0_PROJECT_JSON" | "$_MEM0_PY_BIN" -c "import sys,json; print(json.load(sys.stdin).get('branch','unknown'))" 2>/dev/null || echo "unknown")
else
  MEM0_PROJECT_ID="unknown"
  MEM0_BRANCH="unknown"
fi
export MEM0_PROJECT_ID MEM0_BRANCH
