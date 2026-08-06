#!/usr/bin/env bash
# MCP entry: resolves the venv python (installing deps on first run) and
# launches the local stdio MCP proxy.  Used by .mcp.json / .codex-mcp.json.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="${CLAUDE_PLUGIN_DATA:-${HOME}/.mem0/local}"

VENV_PY=""
[ -x "${DATA_DIR}/venv/bin/python3" ] && VENV_PY="${DATA_DIR}/venv/bin/python3"
[ -z "$VENV_PY" ] && [ -x "${DATA_DIR}/venv/Scripts/python.exe" ] && VENV_PY="${DATA_DIR}/venv/Scripts/python.exe"

if [ -z "$VENV_PY" ]; then
  # First run: install dependencies, then retry
  bash "${SCRIPT_DIR}/ensure_deps.sh" >/dev/null 2>&1 || true
  [ -x "${DATA_DIR}/venv/bin/python3" ] && VENV_PY="${DATA_DIR}/venv/bin/python3"
  [ -z "$VENV_PY" ] && [ -x "${DATA_DIR}/venv/Scripts/python.exe" ] && VENV_PY="${DATA_DIR}/venv/Scripts/python.exe"
fi

PY_BIN="${VENV_PY:-python}"
export PYTHONPATH="${PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
exec "${PY_BIN}" "${SCRIPT_DIR}/../service/mcp_proxy.py"
