# Source this file. Resolves the python interpreter (venv first, then
# system `python`) and provides `_mem0_jq` — a jq-free JSON extractor
# for hook stdin.
#
# Why: hook scripts may not assume jq is installed, and `python3` on
# Windows is often the Microsoft Store stub (a 0-byte shim in
# WindowsApps) that does nothing.  The daemon venv python is preferred;
# otherwise `python` (real CPython, e.g. Python 3.12) is used.

_MEM0_PY=""

_mem0_python() {
  # Prints the resolved python path (callers use command substitution).
  local data_dir="${CLAUDE_PLUGIN_DATA:-$HOME/.mem0/local}"
  local cand p
  # 1) venv python created by ensure_deps.sh (Windows Scripts/ vs Unix bin/)
  for cand in "$data_dir/venv/Scripts/python.exe" \
              "$data_dir/venv/bin/python3" \
              "$data_dir/venv/bin/python"; do
    [ -x "$cand" ] && { _MEM0_PY="$cand"; printf '%s' "$_MEM0_PY"; return 0; }
  done
  # 2) system python — skip the WindowsApps Store stub
  for cand in python python3; do
    p="$(command -v "$cand" 2>/dev/null)" || continue
    case "$p" in
      */WindowsApps/*) continue ;;
    esac
    _MEM0_PY="$cand"
    printf '%s' "$_MEM0_PY"
    return 0
  done
  _MEM0_PY="python"
  printf '%s' "$_MEM0_PY"
}

# Force UTF-8 on stdio. On Windows the locale default is often GBK/cp936,
# which mangles the UTF-8 JSON payloads Claude Code writes to stdin.
_mem0_py_utf8() {
  printf '%s' \
    "import sys; [getattr(s, 'reconfigure', lambda **k: None)(encoding='utf-8', errors='replace') for s in (sys.stdin, sys.stdout)]"
}

# _mem0_jq EXPR [DEFAULT] < input.json
#   EXPR:   `.a.b // .a.c` — first existing key wins
#   DEFAULT: fallback value when no key resolves
_mem0_jq() {
  local expr="${1:-}" default="${2:-}"
  "$(_mem0_python)" -c "
import json, sys
sys.stdin.reconfigure(encoding='utf-8', errors='replace')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
expr = sys.argv[1]
default = sys.argv[2] if len(sys.argv) > 2 else ''
data = json.load(sys.stdin)
for part in expr.split('//'):
    part = part.strip()
    if not part or part == '.':
        continue
    node = data
    ok = True
    for key in part.lstrip('.').split('.'):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            ok = False
            break
    if ok and node is not None:
        print(node if isinstance(node, str) else json.dumps(node, ensure_ascii=False))
        sys.exit(0)
print(default)
" "$expr" "$default"
}

# _mem0_hook_json CTX — print a UserPromptSubmit hook JSON payload
# (the `hookSpecificOutput` envelope jq -cn used to build).
_mem0_hook_json() {
  local ctx="$1"
  "$(_mem0_python)" -c "
import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
print(json.dumps({'hookSpecificOutput': {
    'hookEventName': 'UserPromptSubmit',
    'additionalContext': sys.argv[1],
}}))
" "$ctx"
}
