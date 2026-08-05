"""Scope routing: namespace derivation and LLM-based scope classification.

Isolation is enforced server-side through internal namespaces:

    global    -> u:{user_hash}:global
    workspace -> u:{user_hash}:workspace:{workspace_hash}

The namespace is derived from the *registered* session (SessionStart hook
registers session -> workspace), never from agent-supplied parameters.
Requests for an unregistered session fall back to a one-time workspace
derived from the request's own cwd — never to another workspace.

When the user asks to store with ``scope=auto`` (or the pipeline needs to
classify), the independent Memory LLM decides global | workspace | discard;
on any LLM failure we default to ``workspace`` so nothing leaks globally.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from . import config as app_config
from . import security
from .provider_factory import chat_completion, extract_json_object

log = logging.getLogger("mem0-rvaim.scope")

SCOPE_GLOBAL = "global"
SCOPE_WORKSPACE = "workspace"
SCOPE_DISCARD = "discard"

_CLASSIFY_SYSTEM = (
    "You classify conversation fragments for a memory system. "
    "Respond with a single JSON object only: {\"scope\": \"global\" | \"workspace\" | \"discard\", \"reason\": \"<short reason>\"}.\n"
    "Rules:\n"
    "- global: long-term user preferences, cross-project habits, personal facts about the user "
    "('user prefers Chinese', 'user works at Acme Corp', 'user dislikes X').\n"
    "- workspace: anything about code, files, repositories, technical decisions, project tasks.\n"
    "- discard: casual greetings, one-off operations, low-value small talk, pure chit-chat.\n"
    "- When in doubt, choose workspace."
)


def namespace_for(session_workspace: Optional[str], scope: str, cwd: Optional[str] = None) -> str:
    """Map a resolved scope to the internal namespace string."""
    if scope == SCOPE_GLOBAL:
        return security.global_namespace()
    if scope == SCOPE_WORKSPACE:
        workspace_id = session_workspace or fallback_workspace_id(cwd)
        return security.workspace_namespace(workspace_id)
    raise ValueError(f"invalid scope: {scope}")


def fallback_workspace_id(cwd: Optional[str]) -> str:
    """Last-resort workspace id for unregistered sessions."""
    import os

    if cwd:
        return os.path.basename(os.path.normpath(cwd)) or "unknown"
    return "unknown"


def resolve_scope_for_request(
    requested: str,
    session_workspace: Optional[str],
    messages_text: str,
    cfg: Dict[str, Any],
) -> Tuple[str, str]:
    """Return (scope, reason).  requested: auto | global | workspace."""
    if requested == SCOPE_GLOBAL:
        return SCOPE_GLOBAL, "explicit"
    if requested == SCOPE_WORKSPACE:
        return SCOPE_WORKSPACE, "explicit"
    return classify_scope(messages_text, cfg)


def classify_scope(messages_text: str, cfg: Dict[str, Any]) -> Tuple[str, str]:
    """Ask the independent Memory LLM to classify a fragment."""
    llm_cfg = dict(cfg.get("llm") or {})
    if not llm_cfg.get("api_key") and llm_cfg.get("base_url", "").find("openai.com") >= 0:
        log.debug("No LLM API key configured; defaulting to workspace scope")
        return SCOPE_WORKSPACE, "llm-unavailable"
    try:
        reply = chat_completion(
            llm_cfg,
            [
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": messages_text[:6000]},
            ],
            json_mode=True,
            timeout=30.0,
        )
        parsed = extract_json_object(reply)
        scope = parsed.get("scope", SCOPE_WORKSPACE)
        if scope not in (SCOPE_GLOBAL, SCOPE_WORKSPACE, SCOPE_DISCARD):
            scope = SCOPE_WORKSPACE
        return scope, str(parsed.get("reason", ""))[:200]
    except Exception as exc:  # never break capture on classification failure
        log.warning("scope classification failed (%s); defaulting to workspace", exc)
        return SCOPE_WORKSPACE, "classifier-error"


def build_metadata(
    scope: str,
    workspace_id: str,
    session_id: str,
    memory_type: str,
    source: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Common metadata attached to every memory (immutable by clients)."""
    metadata: Dict[str, Any] = {
        "actual_user_id": security.user_hash(),
        "scope": scope,
        "workspace_id": workspace_id,
        "workspace_name": workspace_id,
        "session_id": session_id,
        "memory_type": memory_type,
        "source": source,
        "created_at": _now(),
        "updated_at": _now(),
    }
    if extra:
        # clients may add non-authoritative tags (type, confidence, branch...)
        for key in ("type", "confidence", "branch", "files_touched", "importance"):
            if key in extra and extra[key] is not None:
                metadata[key] = extra[key]
    return metadata


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
