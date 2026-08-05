"""Shared local search helper (replaces the cloud _search.py).

All searches go through the daemon's /v1/memories/search or /v1/recall.
No cloud endpoint, no API key.  Keep the same public function names so
other scripts/skills keep working.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from service import bootstrap  # noqa: E402


def should_rerank() -> bool:
    """Kept for compatibility; local search needs no reranking."""
    raw = os.environ.get("MEM0_RERANK")
    if raw is None:
        return False
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def search_memories(
    api_key: str,
    user_id: str,
    project_id: str,
    query: str,
    metadata_type: str | None = None,
    metadata_filters: dict | None = None,
    top_k: int = 3,
    min_score: float = 0.0,
    rerank: bool = False,
    threshold: float = 0.3,
    global_search: bool = False,
) -> list[dict]:
    """Local search: delegates to the daemon (api_key is ignored)."""
    session_id = os.environ.get("MEM0_SESSION_ID", "")
    if not session_id:
        try:
            sid_file = f"/tmp/mem0_session_id_{os.environ.get('USER', 'default')}"
            with open(sid_file) as fh:
                session_id = fh.read().strip()
        except OSError:
            session_id = ""
    if not bootstrap.is_healthy(timeout=1.5):
        return []
    scope = "global" if global_search else "both"
    try:
        result = bootstrap.request("POST", "/v1/memories/search", {
            "query": query,
            "session_id": session_id,
            "scope": scope,
            "top_k": top_k,
            "threshold": threshold,
        }, timeout=5.0)
    except Exception:
        return []
    merged: list[dict] = []
    for bucket in (result.get("results") or {}).values():
        merged.extend(bucket)
    if min_score > 0:
        merged = [m for m in merged if (m.get("score") or 0) >= min_score]
    return merged[:top_k]


def format_results_for_context(
    memories: list[dict],
    heading: str = "Relevant memories",
) -> str:
    if not memories:
        return ""
    lines = [f"### {heading}", ""]
    for m in memories:
        mid = m.get("id", "?")[:8]
        text = m.get("memory", "")[:200]
        cat = (m.get("metadata") or {}).get("type", "unknown")
        lines.append(f"- [{cat}] {text} [mem0:{mid}]")
    lines.append("")
    return "\n".join(lines)
