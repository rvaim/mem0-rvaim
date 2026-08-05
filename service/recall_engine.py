"""Recall: dual-namespace retrieval with merge, dedup and token budget.

Flow:
    user message -> [optional rewrite: Recall LLM generates <=3 queries]
    -> embed query
    -> search workspace namespace (top N)
    -> search global namespace (top M)
    -> optional session-summary search (top K)
    -> merge / dedup / score -> cap at max_tokens -> formatted context

Every failure degrades to fewer/empty results; recall never blocks the
host agent beyond the configured timeout (enforced by the daemon route).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from . import config as app_config
from .memory_engine import MemoryEngine
from .provider_factory import chat_completion
from .scope_router import namespace_for

log = logging.getLogger("mem0-rvaim.recall")

_REWRITE_SYSTEM = (
    "You rewrite a user query into up to 3 independent search queries for a "
    "memory retrieval system. Each query should target a different angle "
    "(facts, decisions, preferences, past problems). "
    'Respond with JSON only: {"queries": ["q1", "q2"]} (1-3 items, keep each under 80 tokens).'
)


class RecallEngine:
    def __init__(self, engine: MemoryEngine, cfg: Optional[Dict[str, Any]] = None):
        self.engine = engine
        self.cfg = cfg or app_config.load_config()

    def recall(
        self,
        query: str,
        workspace_id: str,
        session_id: str,
        mode: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        recall_cfg = self.cfg.get("recall") or {}
        mode = mode or recall_cfg.get("mode", "direct")
        workspace_top = top_k or int(recall_cfg.get("workspace_top_k", 8))
        global_top = int(recall_cfg.get("global_top_k", 4))
        session_top = int(recall_cfg.get("session_top_k", 3))
        threshold = float(recall_cfg.get("threshold", 0.3))
        max_tokens = int(recall_cfg.get("max_tokens", 1600))

        queries = [query]
        if mode == "rewrite":
            rewritten = self._rewrite(query)
            if rewritten:
                queries = rewritten[:3]

        merged: Dict[str, Dict[str, Any]] = {}
        namespace = namespace_for(workspace_id, "workspace", None)
        for q in queries:
            for m in self.engine.search(namespace, q, top_k=workspace_top, threshold=threshold):
                _merge(merged, m, boost=1.2)
            for m in self.engine.search(security_global(), q, top_k=global_top, threshold=threshold):
                _merge(merged, m, boost=1.0)

        # session summaries are workspace-scoped, recalled separately
        if session_id:
            for m in self.engine.get_all(namespace, top_k=session_top, memory_type="session_summary"):
                _merge(merged, m, boost=0.9, always=True)

        ranked = sorted(merged.values(), key=lambda m: m.get("_score", 0.0), reverse=True)
        context, used = self._format_context(ranked, max_tokens)
        return {
            "query": query,
            "queries": queries,
            "mode": mode,
            "memories": ranked,
            "used_tokens": used,
            "budget": max_tokens,
            "context": context,
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _rewrite(self, query: str) -> List[str]:
        llm_cfg = dict(self.cfg.get("recall_llm") or self.cfg.get("llm") or {})
        if not llm_cfg.get("api_key") and "openai.com" in (llm_cfg.get("base_url") or ""):
            return []
        try:
            from .provider_factory import extract_json_object

            reply = chat_completion(
                llm_cfg,
                [
                    {"role": "system", "content": _REWRITE_SYSTEM},
                    {"role": "user", "content": query[:2000]},
                ],
                json_mode=True,
                timeout=20.0,
            )
            parsed = extract_json_object(reply)
            queries = [str(q).strip()[:500] for q in parsed.get("queries", []) if str(q).strip()]
            return queries[:3]
        except Exception as exc:
            log.warning("query rewrite failed (%s); using direct query", exc)
            return []

    def _format_context(self, memories: List[Dict[str, Any]], max_tokens: int) -> Tuple[str, int]:
        if not memories:
            return "", 0
        lines = ["### 相关记忆 (mem0-rvaim)", ""]
        used = 0
        for m in memories:
            text = str(m.get("memory", ""))[:400]
            meta = m.get("metadata") or {}
            scope = meta.get("scope", "?")
            mtype = meta.get("memory_type") or meta.get("type") or "memory"
            mid = str(m.get("id", "?"))[:8]
            line = f"- [{scope}/{mtype}] {text} [mem0:{mid}]"
            tokens = max(1, len(line) // 4)  # rough token estimate
            if used + tokens > max_tokens:
                break
            lines.append(line)
            used += tokens
        lines.append("")
        return "\n".join(lines), used


def security_global() -> str:
    from . import security

    return security.global_namespace()


def _merge(
    merged: Dict[str, Dict[str, Any]],
    memory: Dict[str, Any],
    boost: float = 1.0,
    always: bool = False,
) -> None:
    """Merge search results by memory id, keeping the best score."""
    mid = str(memory.get("id", ""))
    if not mid:
        return
    score = float(memory.get("score") or 0.0)
    if always:
        score = max(score, 0.5)
    if mid in merged:
        merged[mid]["_score"] = max(merged[mid]["_score"], score * boost)
        return
    entry = dict(memory)
    entry["_score"] = score * boost
    entry["_source_scope"] = (memory.get("metadata") or {}).get("scope", "?")
    merged[mid] = entry
