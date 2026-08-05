#!/usr/bin/env bash
# PreToolUse hook for mem0-rvaim MCP tools.
#
# Local version: identity injection is REMOVED. The daemon derives user
# and workspace namespaces from the registered session — the agent cannot
# and must not supply user_id/app_id. This hook only exists as a
# compatibility no-op so official-style hook wiring does not break.
#
# Exit 0 always (allow the call through unchanged).

set -uo pipefail

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null || echo "")

case "$TOOL_NAME" in
  mcp__mem0__*|mcp__plugin_mem0_mem0__*|mcp__mem0-rvaim__*)
    # pass through untouched; daemon enforces scope server-side
    ;;
  *)
    exit 0
    ;;
esac

exit 0
