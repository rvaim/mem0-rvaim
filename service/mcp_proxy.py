"""Lightweight stdio MCP server (JSON-RPC 2.0) that proxies to the daemon.

Claude Code / Codex launch this process as a local MCP server.  It:
    * ensures the daemon is running before answering tool calls
    * forwards every tools/call to the daemon HTTP API
    * never loads mem0 / never opens Qdrant / never embeds

The session id comes from the hook-injected environment
(``MEM0_SESSION_ID``, fallback ``CLAUDE_SESSION_ID``) so that workspace
routing is inherited from the SessionStart registration.
"""

if __name__ == "__main__" and __package__ in (None, ""):
    # allow direct execution from anywhere (relative imports need package mode)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    import service.mcp_proxy as _entry

    raise SystemExit(_entry.main())

import json
import logging
import os
import sys
import threading
from typing import Any, Dict, List, Optional

from . import bootstrap
from . import security
from .bootstrap import request

log = logging.getLogger("mem0-rvaim.mcp")

PROTOCOL_VERSION = "2025-03-26"

# ---------------------------------------------------------------------------
# tool registry (official tool names preserved)
# ---------------------------------------------------------------------------
def _t(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties, "required": required},
    }


TOOLS: List[Dict[str, Any]] = [
    _t(
        "add_memory",
        "Store a memory. scope=auto lets the independent Memory LLM classify "
        "global (cross-project facts) vs workspace (project-specific).",
        {
            "content": {"type": "string", "description": "Text to store"},
            "messages": {"type": "array", "items": {"type": "object"},
                         "description": "Conversation messages [{role, content}]"},
            "scope": {"type": "string", "enum": ["auto", "global", "workspace"], "default": "auto"},
            "infer": {"type": "boolean", "description": "Let the Memory LLM extract facts (default true for messages)"},
            "metadata": {"type": "object"},
        },
        [],
    ),
    _t(
        "search_memories",
        "Search stored memories. scope=both searches workspace and global namespaces.",
        {
            "query": {"type": "string"},
            "scope": {"type": "string", "enum": ["both", "global", "workspace"], "default": "both"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 100},
            "threshold": {"type": "number", "minimum": 0, "maximum": 1},
            "memory_type": {"type": "string"},
        },
        ["query"],
    ),
    _t(
        "get_memories",
        "List stored memories for the current session's scope (no semantic query).",
        {
            "scope": {"type": "string", "enum": ["both", "global", "workspace"], "default": "workspace"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 500},
            "memory_type": {"type": "string"},
        },
        [],
    ),
    _t(
        "get_memory",
        "Fetch a single memory by ID.",
        {"memory_id": {"type": "string"}},
        ["memory_id"],
    ),
    _t(
        "update_memory",
        "Update a memory's text and/or metadata (metadata_patch replaces only given keys).",
        {
            "memory_id": {"type": "string"},
            "text": {"type": "string"},
            "metadata_patch": {"type": "object"},
        },
        ["memory_id"],
    ),
    _t(
        "delete_memory",
        "Delete a single memory by ID.",
        {"memory_id": {"type": "string"}},
        ["memory_id"],
    ),
    _t(
        "delete_all_memories",
        "Delete all memories in the given scope (global or current workspace; never both).",
        {"scope": {"type": "string", "enum": ["global", "workspace"], "default": "workspace"}},
        [],
    ),
    _t(
        "delete_entities",
        "Delete entities (proper nouns) extracted from memories.",
        {
            "entity_names": {"type": "array", "items": {"type": "string"}},
            "entity_type": {"type": "string"},
        },
        ["entity_names"],
    ),
    _t(
        "list_entities",
        "List extracted entities (people, orgs, products...) for the current workspace.",
        {"entity_type": {"type": "string"}, "limit": {"type": "integer"}},
        [],
    ),
    _t(
        "recall_context",
        "Retrieve relevant memories for the current task. The daemon generates the "
        "queries itself (no multi-query fan-out by the agent required).",
        {
            "query": {"type": "string", "description": "Task or question to recall context for"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
            "mode": {"type": "string", "enum": ["direct", "rewrite"]},
        },
        ["query"],
    ),
    _t(
        "memory_health",
        "Check daemon, vector store, session registration and counts.",
        {},
        [],
    ),
    _t(
        "list_workspaces",
        "List known workspaces with memory counts.",
        {},
        [],
    ),
    _t(
        "consolidate_memories",
        "Ask the independent Memory LLM to find duplicates/contradictions. "
        "dry_run=true only returns suggestions; false applies them.",
        {"dry_run": {"type": "boolean", "default": True}},
        [],
    ),
    _t(
        "get_event_status",
        "Compatibility shim: local writes complete synchronously, so status is always SUCCEEDED.",
        {"event_id": {"type": "string"}},
        ["event_id"],
    ),
]


class McpProxy:
    def __init__(self) -> None:
        self.session_id = (
            os.environ.get("MEM0_SESSION_ID")
            or os.environ.get("CLAUDE_SESSION_ID")
            or ""
        )
        self.cwd = os.environ.get("MEM0_CWD") or os.getcwd()
        self._lock = threading.Lock()
        self._daemon_ready = False

    # ------------------------------------------------------------------
    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method", "")
        msg_id = message.get("id")
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mem0-rvaim", "version": "0.3.0"},
                },
            }
        if method == "notifications/initialized" or method == "notifications/cancelled":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            return self._call_tool(msg_id, message.get("params", {}))
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    # ------------------------------------------------------------------
    def _ensure_daemon(self) -> bool:
        with self._lock:
            if self._daemon_ready and bootstrap.is_healthy(timeout=2.0):
                return True
            result = bootstrap.ensure_daemon(timeout=60.0)
            self._daemon_ready = bool(result.get("ok"))
            return self._daemon_ready

    def _call_tool(self, msg_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if not self._ensure_daemon():
            return self._error(msg_id, -32000, "mem0 daemon is not available")
        try:
            result = self._dispatch(name, arguments)
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
            ]}}
        except Exception as exc:
            log.warning("tool %s failed: %s", name, exc)
            return self._error(msg_id, -32603, f"{name} failed: {exc}")

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}

    # ------------------------------------------------------------------
    def _dispatch(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        session = self.session_id or "session-unknown"
        if name == "add_memory":
            return request("POST", "/v1/memories/add", {
                "content": args.get("content"),
                "messages": args.get("messages"),
                "scope": args.get("scope", "auto"),
                "infer": args.get("infer"),
                "metadata": args.get("metadata"),
                "session_id": session,
            })
        if name == "search_memories":
            return request("POST", "/v1/memories/search", {
                "query": args["query"],
                "session_id": session,
                "scope": args.get("scope", "both"),
                "top_k": args.get("top_k"),
                "threshold": args.get("threshold"),
                "memory_type": args.get("memory_type"),
            })
        if name == "get_memories":
            return request("POST", "/v1/memories/get", {
                "session_id": session,
                "scope": args.get("scope", "workspace"),
                "top_k": args.get("top_k"),
                "memory_type": args.get("memory_type"),
            })
        if name == "get_memory":
            return request("GET", f"/v1/memories/{args['memory_id']}")
        if name == "update_memory":
            return request("PATCH", f"/v1/memories/{args['memory_id']}", {
                "text": args.get("text"),
                "metadata_patch": args.get("metadata_patch"),
                "session_id": session,
            })
        if name == "delete_memory":
            return request("DELETE", f"/v1/memories/{args['memory_id']}")
        if name == "delete_all_memories":
            return request("POST", "/v1/memories/delete_all", {
                "session_id": session,
                "scope": args.get("scope", "workspace"),
            })
        if name == "delete_entities":
            return request("POST", "/v1/entities/delete", {
                "session_id": session,
                "entity_names": args.get("entity_names", []),
                "entity_type": args.get("entity_type"),
            })
        if name == "list_entities":
            return request("POST", "/v1/entities/list", {
                "session_id": session,
                "entity_type": args.get("entity_type"),
                "limit": args.get("limit"),
            })
        if name == "recall_context":
            return request("POST", "/v1/recall", {
                "query": args["query"],
                "session_id": session,
                "top_k": args.get("top_k"),
                "mode": args.get("mode"),
            })
        if name == "memory_health":
            health = request("GET", "/health")
            stats = request("GET", "/v1/stats")
            return {**health, "stats": stats, "session_id": session}
        if name == "list_workspaces":
            return request("GET", "/v1/workspaces")
        if name == "consolidate_memories":
            return request("POST", "/v1/memories/consolidate", {
                "session_id": session,
                "dry_run": args.get("dry_run", True),
            })
        if name == "get_event_status":
            return request("GET", f"/v1/events/{args['event_id']}")
        raise ValueError(f"unknown tool: {name}")


def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("MEM0_DEBUG") else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    proxy = McpProxy()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            response = proxy.handle(message)
        except Exception as exc:  # never die on a malformed request
            log.error("handler error: %s", exc)
            response = {"jsonrpc": "2.0", "id": message.get("id"),
                        "error": {"code": -32603, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__" and __package__ in (None, ""):
    # allow direct execution: `python service/mcp_proxy.py` from anywhere
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from service.mcp_proxy import main

    raise SystemExit(main())
elif __name__ == "__main__":
    raise SystemExit(main())
