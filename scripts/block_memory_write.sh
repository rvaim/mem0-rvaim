#!/usr/bin/env bash
# Hook: PreToolUse (matcher: Write|Edit)
#
# Blocks writes to MEMORY.md and auto-memory files, redirecting Claude
# to the local mem0-rvaim add_memory tool instead.
#
# Input:  JSON on stdin with tool_name, tool_input
# Output: stderr message (exit 2 = block)
#
# Exit codes:
#   0 = allow the tool call
#   2 = block the tool call (stderr is shown to Claude as feedback)

set -euo pipefail

if [ -n "${MEM0_DEBUG:-}" ]; then
  mkdir -p "$HOME/.mem0" && exec 2>>"$HOME/.mem0/hooks.log"
fi

INPUT=$(cat)

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // ""' 2>/dev/null || echo "")

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

case "$FILE_PATH" in
  */.claude/*/MEMORY.md|*/.claude/memory/*|*/.mem0/local/data/*)
    echo "BLOCKED: Do not write to $FILE_PATH. Use the mem0-rvaim \`add_memory\` MCP tool instead to persist memories (or \`mem0:remember\`). The local memory daemon owns all memory storage." >&2
    exit 2
    ;;
  *)
    exit 0
    ;;
esac
