# Source this file. Resolves MEM0_PROJECT_ID (workspace) and branch.
# Local only — no cloud calls.

_SCRIPT_DIR="$( cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd )"

if command -v python3 >/dev/null 2>&1; then
  _MEM0_PROJECT_JSON=$(PYTHONPATH="$_SCRIPT_DIR" python3 -c "
import json, sys
sys.path.insert(0, '$_SCRIPT_DIR')
from _project import resolve_workspace_id, resolve_branch
cwd = '$PWD'
print(json.dumps({'workspace_id': resolve_workspace_id(cwd), 'branch': resolve_branch(cwd)}))
" 2>/dev/null || echo '{"workspace_id":"unknown","branch":"unknown"}')
  MEM0_PROJECT_ID=$(echo "$_MEM0_PROJECT_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('workspace_id','unknown'))" 2>/dev/null || echo "unknown")
  MEM0_BRANCH=$(echo "$_MEM0_PROJECT_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('branch','unknown'))" 2>/dev/null || echo "unknown")
else
  MEM0_PROJECT_ID="unknown"
  MEM0_BRANCH="unknown"
fi
export MEM0_PROJECT_ID MEM0_BRANCH
