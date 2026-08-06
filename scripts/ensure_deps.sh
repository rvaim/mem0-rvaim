#!/usr/bin/env bash
# Create the persistent venv, install pinned dependencies and make sure the
# daemon is running.  Runs on SessionStart; skips if requirements.txt hasn't
# changed.  Extends the official installer (same layout, lock + stamp) but
# with Windows support and daemon bootstrapping.
set -uo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA_DIR="${CLAUDE_PLUGIN_DATA:-${HOME}/.mem0/local}"
VENV_DIR="${DATA_DIR}/venv"
REQ_SRC="${PLUGIN_ROOT}/requirements.txt"
REQ_STAMP="${DATA_DIR}/requirements.txt"

# Resolve python inside the venv (Unix bin/ vs Windows Scripts/)
find_venv_python() {
  if [ -x "${VENV_DIR}/bin/python3" ]; then
    printf '%s' "${VENV_DIR}/bin/python3"
  elif [ -x "${VENV_DIR}/bin/python" ]; then
    printf '%s' "${VENV_DIR}/bin/python"
  elif [ -x "${VENV_DIR}/Scripts/python.exe" ]; then
    printf '%s' "${VENV_DIR}/Scripts/python.exe"
  else
    printf ''
  fi
}

find_venv_pip() {
  if [ -x "${VENV_DIR}/bin/pip" ]; then
    printf '%s' "${VENV_DIR}/bin/pip"
  elif [ -x "${VENV_DIR}/Scripts/pip.exe" ]; then
    printf '%s' "${VENV_DIR}/Scripts/pip.exe"
  else
    printf ''
  fi
}

mkdir -p "${DATA_DIR}"
LOCKDIR="${DATA_DIR}/.install-lock"

needs_install=false
VENV_PY="$(find_venv_python)"

if [ -z "$VENV_PY" ]; then
  needs_install=true
elif ! diff -q "${REQ_SRC}" "${REQ_STAMP}" >/dev/null 2>&1; then
  needs_install=true
fi

if [ "${needs_install}" = "true" ]; then
  if mkdir "${LOCKDIR}" 2>/dev/null; then
    # We acquired the lock — proceed with installation
    trap 'rmdir "${LOCKDIR}" 2>/dev/null || true' EXIT
    # `python3` alone may be a Microsoft Store stub on Windows — prefer
    # plain `python`, which is a real CPython install on Windows.
    python -m venv "${VENV_DIR}" 2>/dev/null || python3 -m venv "${VENV_DIR}" || true
    VENV_PIP="$(find_venv_pip)"
    if [ -n "$VENV_PIP" ]; then
      "${VENV_PIP}" install --quiet --upgrade pip >/dev/null 2>&1 || true
      if "${VENV_PIP}" install --quiet -r "${REQ_SRC}" 2>/dev/null; then
        cp "${REQ_SRC}" "${REQ_STAMP}"
        rm -f "${DATA_DIR}/.install-failed"
      else
        rm -f "${REQ_STAMP}"
        touch "${DATA_DIR}/.install-failed"
        echo "mem0-rvaim: failed to install Python dependencies" >&2
        exit 0
      fi
    else
      rm -f "${REQ_STAMP}"
      touch "${DATA_DIR}/.install-failed"
      echo "mem0-rvaim: venv creation failed" >&2
      exit 0
    fi
  else
    # Another process holds the lock — wait up to 60s for it to finish
    for _i in $(seq 1 60); do
      [ ! -d "${LOCKDIR}" ] && break
      sleep 1
    done
    if [ -f "${DATA_DIR}/.install-failed" ]; then
      echo "mem0-rvaim: dependency installation failed (by another session)" >&2
    fi
  fi
fi

# Start the daemon (idempotent). Prefer the venv python when present.
VENV_PY="$(find_venv_python)"
PY_BIN="${VENV_PY:-python}"
MEM0_LOCAL_ROOT="${DATA_DIR}" MEM0_CWD="${PWD}" \
  PYTHONPATH="${PLUGIN_ROOT}" "${PY_BIN}" -c "
import sys
sys.path.insert(0, '${PLUGIN_ROOT}')
from service.client import ensure_daemon
ok = ensure_daemon()
sys.exit(0 if ok else 1)
" >/dev/null 2>&1 || true

exit 0
