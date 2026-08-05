"""Plugin surface: required skills, manifests, hooks wiring, no cloud rubric."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_SKILLS = [
    "remember", "peek", "forget", "pin", "health", "stats", "tour",
    "context-loader", "dream", "memory-reviewer", "export", "import",
    "list-projects", "switch-project", "onboard",
]


def test_all_required_skills_present():
    for skill in REQUIRED_SKILLS:
        skill_md = ROOT / "skills" / skill / "SKILL.md"
        assert skill_md.is_file(), f"missing skill: {skill}"
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---"), f"{skill}/SKILL.md missing frontmatter"
        assert "description:" in text.split("---")[1]


def test_hooks_wired():
    hooks = json.loads((ROOT / "hooks.json").read_text(encoding="utf-8"))
    events = hooks["hooks"]
    assert "SessionStart" in events
    assert "UserPromptSubmit" in events
    assert "PreCompact" in events
    assert "Stop" in events
    commands = []
    for handlers in events.values():
        for handler in handlers:
            for h in handler.get("hooks", []):
                commands.append(h.get("command", ""))
    joined = " ".join(commands)
    assert "on_session_start.sh" in joined
    assert "on_user_prompt.sh" in joined
    assert "on_stop.sh" in joined
    assert "on_pre_compact.sh" in joined
    assert "ensure_deps.sh" in joined


def test_mcp_config_is_local_stdio():
    for name in (".mcp.json", ".codex-mcp.json", "mcp_config.json"):
        cfg = json.loads((ROOT / name).read_text(encoding="utf-8"))
        server = cfg["mcpServers"]["mem0"]
        assert server["type"] == "stdio"
        assert "mcp_entry.sh" in " ".join(server.get("args", []))
        assert "http" not in server.get("type", "")


def test_no_agent_search_rubric_in_skills():
    """Skills must not instruct the host agent to build multi-query searches."""
    for skill in REQUIRED_SKILLS:
        text = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "run 2-4 parallel" not in text.lower()
        assert "parallel `search_memories`" not in text.lower()


def test_mcp_tool_names_match_manifest():
    from service.mcp_proxy import TOOLS

    names = {t["name"] for t in TOOLS}
    assert {"add_memory", "search_memories", "get_memories", "get_memory",
            "update_memory", "delete_memory", "delete_all_memories",
            "delete_entities", "list_entities"} <= names
    assert {"recall_context", "memory_health", "list_workspaces",
            "consolidate_memories"} <= names
