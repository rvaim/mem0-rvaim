"""Guarantee: no cloud endpoints or MEM0_API_KEY anywhere in runtime code."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# files that are allowed to mention cloud tokens (docs/migration only)
EXCLUDED = {
    "README.md",  # migration notes must mention them (whitelisted below)
    "docs/migration-from-mem0-cloud.md",
}

FORBIDDEN = [
    r"api\.mem0\.ai",
    r"mcp\.mem0\.ai",
    r"MEM0_API_KEY",
]


def _iter_source_files():
    for sub in ("service", "scripts", "skills"):
        base = ROOT / sub
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in (".py", ".sh", ".md", ".json", ".txt"):
                yield path
    for name in (".mcp.json", ".codex-mcp.json", "mcp_config.json",
                 "hooks.json", "requirements.txt", "AGENTS.md",
                 "plugin.json"):
        path = ROOT / name
        if path.is_file():
            yield path


def test_no_cloud_endpoints_in_runtime_code():
    violations = []
    for path in _iter_source_files():
        if path.name in EXCLUDED:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in FORBIDDEN:
            if re.search(pattern, content):
                violations.append(f"{path.relative_to(ROOT)}: {pattern}")
    assert not violations, f"cloud references found:\n" + "\n".join(violations)


def test_no_api_key_required_in_plugin_manifest():
    for name in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        import json

        manifest = json.loads((ROOT / name).read_text(encoding="utf-8"))
        user_config = manifest.get("userConfig") or {}
        keys = {k.lower() for k in user_config.keys()}
        assert "api_key" not in keys and "mem0_api_key" not in keys, \
            f"{name} must not require an API key"


def test_requirements_have_no_cloud_sdk():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "mem0ai" in req
    assert "qdrant-client" in req
    assert "platform" not in req.lower()
