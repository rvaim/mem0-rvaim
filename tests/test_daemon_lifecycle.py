"""Daemon lifecycle: start, health, session registration, graceful stop,
and data survival across restart (plugin upgrade scenario)."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from service import config as app_config  # noqa: E402
from service.daemon import DaemonApp, create_server, run_server  # noqa: E402
from service.bootstrap import request  # noqa: E402


@pytest.fixture()
def daemon_app(mem0_env):
    app = DaemonApp(mem0_env["data_dir"], mem0_env["config"])
    yield app
    app.shutdown()


def test_health_ok(daemon_app):
    health = daemon_app.health()
    assert health["ok"] is True
    assert health["version"]
    assert "degraded" in health


def test_register_session_and_routing(daemon_app):
    result = daemon_app.register_session(
        type("S", (), {"session_id": "s1", "cwd": "/tmp/proj-a",
                       "workspace_id": "proj-a", "host": "claude",
                       "pid": 1234, "client": None})()
    )
    assert result["ok"] is True
    assert result["workspace_id"] == "proj-a"
    assert result["namespace"].startswith("u:")
    # unknown session falls back to its own cwd, never another workspace
    from service.scope_router import namespace_for
    ws = daemon_app._workspace_for("s-unknown", cwd="/tmp/proj-b")
    assert ws == "proj-b"


def test_stats_and_workspaces(daemon_app):
    daemon_app.register_session(
        type("S", (), {"session_id": "s2", "cwd": "/tmp/proj-a",
                       "workspace_id": "proj-a", "host": "claude",
                       "pid": 1, "client": None})()
    )
    stats = daemon_app.stats("s2")
    assert stats["workspace_id"] == "proj-a"
    assert "memory_counts" in stats
    ws = daemon_app.list_workspaces()
    assert any(w["workspace_id"] == "proj-a" for w in ws["workspaces"])


def test_data_survives_restart(mem0_env):
    """Restart (plugin upgrade) must not lose memories."""
    app1 = DaemonApp(mem0_env["data_dir"], mem0_env["config"])
    app1.register_session(
        type("S", (), {"session_id": "s-restart", "cwd": "/tmp/proj-x",
                       "workspace_id": "proj-x", "host": "claude",
                       "pid": 1, "client": None})()
    )
    result = app1.add_memory(
        type("A", (), {"content": "user prefers coffee", "messages": None,
                       "scope": "workspace", "infer": False, "metadata": None,
                       "session_id": "s-restart", "idempotency_key": None})()
    )
    assert result["ok"] is True
    app1.shutdown()

    app2 = DaemonApp(mem0_env["data_dir"], mem0_env["config"])
    try:
        found = app2.search_memories(
            type("S", (), {"query": "coffee", "session_id": "s-restart",
                           "scope": "workspace", "top_k": 10,
                           "threshold": 0.0, "memory_type": None})()
        )
        workspace_results = found["results"].get("workspace", [])
        assert any("coffee" in str(m.get("memory", "")) for m in workspace_results)
    finally:
        app2.shutdown()


def test_http_server_auth(mem0_env):
    """HTTP layer: requests without token are rejected."""
    server = create_server(mem0_env["data_dir"], 0, mem0_env["config"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        import json
        import urllib.error
        import urllib.request

        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=5)
        assert excinfo.value.code == 401

        # with token
        token = server.daemon_token  # type: ignore[attr-defined]
        req2 = urllib.request.Request(f"http://127.0.0.1:{port}/health",
                                      headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req2, timeout=5) as resp:
            data = json.loads(resp.read())
            assert data["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_bad_scope_rejected(daemon_app):
    """delete_all with an invalid/ambiguous scope is rejected at the
    schema layer before it ever reaches the daemon logic."""
    from pydantic import ValidationError
    from service.schemas import DeleteAllMemoriesRequest

    with pytest.raises(ValidationError):
        DeleteAllMemoriesRequest(session_id="s1", scope="both")
    with pytest.raises(ValidationError):
        DeleteAllMemoriesRequest(session_id="s1", scope="other-ws")
