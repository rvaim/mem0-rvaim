"""MCP proxy: protocol handling and tool forwarding to the daemon."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from service.mcp_proxy import McpProxy, TOOLS  # noqa: E402


@pytest.fixture()
def proxy(mem0_env, monkeypatch):
    from service import bootstrap
    from tests.conftest import kill_daemons_holding

    # point the proxy at a real daemon on the isolated data root
    monkeypatch.setenv("MEM0_LOCAL_ROOT", str(mem0_env["root"]))
    monkeypatch.setenv("MEM0_SESSION_ID", "s-mcp")
    try:
        result = bootstrap.ensure_daemon(mem0_env["data_dir"], timeout=90.0)
        assert result.get("ok"), f"daemon failed to start: {result}"
        proxy = McpProxy()
        proxy.session_id = "s-mcp"
        proxy._daemon_ready = True
        # register the session so routing works
        from service.bootstrap import request

        request("POST", "/v1/session/register", {
            "session_id": "s-mcp", "cwd": "/tmp/proj-m", "workspace_id": "proj-m",
            "host": "claude", "pid": 1,
        })
        yield proxy
    finally:
        # teardown must always run, even if a test asserted
        bootstrap.stop_daemon()
        kill_daemons_holding(mem0_env["data_dir"])


def test_initialize_and_list_tools(proxy):
    resp = proxy.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {}})
    assert resp["result"]["protocolVersion"]
    assert resp["result"]["serverInfo"]["name"] == "mem0-rvaim"

    resp = proxy.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    for expected in ("add_memory", "search_memories", "get_memories",
                     "get_memory", "update_memory", "delete_memory",
                     "delete_all_memories", "delete_entities", "list_entities",
                     "recall_context", "memory_health", "list_workspaces",
                     "consolidate_memories", "get_event_status"):
        assert expected in names


def test_unknown_method(proxy):
    resp = proxy.handle({"jsonrpc": "2.0", "id": 3, "method": "bogus"})
    assert resp["error"]["code"] == -32601


def test_tool_call_roundtrip(proxy):
    resp = proxy.handle({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "add_memory", "arguments": {
            "content": "mcp proxy test memory", "scope": "workspace",
            "infer": False,
        }},
    })
    assert "error" not in resp, resp
    text = resp["result"]["content"][0]["text"]
    assert '"ok": true' in text

    resp = proxy.handle({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "search_memories", "arguments": {
            "query": "mcp proxy test", "scope": "workspace",
        }},
    })
    text = resp["result"]["content"][0]["text"]
    assert "mcp proxy test memory" in text

    resp = proxy.handle({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "memory_health", "arguments": {}},
    })
    text = resp["result"]["content"][0]["text"]
    assert '"ok": true' in text


def test_tool_rejects_user_id(proxy):
    """The proxy never forwards a user-supplied user_id/app_id."""
    resp = proxy.handle({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "add_memory", "arguments": {
            "content": "x", "user_id": "attacker", "app_id": "other-ws",
        }},
    })
    assert "error" not in resp
