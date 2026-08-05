"""Scope isolation: global vs workspace namespaces; forged workspace ids
must never leak memory across workspaces."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from service import security  # noqa: E402
from service.daemon import DaemonApp  # noqa: E402


def _register(app, session_id, workspace_id, cwd):
    app.register_session(
        type("S", (), {"session_id": session_id, "cwd": cwd,
                       "workspace_id": workspace_id, "host": "claude",
                       "pid": 1, "client": None})()
    )


def _add(app, session_id, content, scope="workspace"):
    return app.add_memory(
        type("A", (), {"content": content, "messages": None, "scope": scope,
                       "infer": False, "metadata": None,
                       "session_id": session_id, "idempotency_key": None})()
    )


def _search(app, session_id, query, scope="workspace"):
    result = app.search_memories(
        type("S", (), {"query": query, "session_id": session_id, "scope": scope,
                       "top_k": 20, "threshold": 0.0, "memory_type": None})()
    )
    return result["results"].get(scope, [])


@pytest.fixture()
def app(mem0_env):
    app = DaemonApp(mem0_env["data_dir"], mem0_env["config"])
    yield app
    app.shutdown()


def test_workspace_isolation(app):
    _register(app, "s-a", "workspace-a", "/tmp/a")
    _register(app, "s-b", "workspace-b", "/tmp/b")
    _add(app, "s-a", "workspace A uses PostgreSQL as its database")
    _add(app, "s-b", "workspace B uses SQLite as its database")

    hits_a = _search(app, "s-a", "PostgreSQL")
    assert any("PostgreSQL" in str(m.get("memory", "")) for m in hits_a)
    hits_b = _search(app, "s-b", "PostgreSQL")
    assert all("PostgreSQL" not in str(m.get("memory", "")) for m in hits_b)


def test_global_shared_across_workspaces(app):
    _register(app, "s-a", "workspace-a", "/tmp/a")
    _register(app, "s-b", "workspace-b", "/tmp/b")
    _add(app, "s-a", "user prefers Chinese answers", scope="global")

    for session in ("s-a", "s-b"):
        hits = _search(app, session, "Chinese", scope="global")
        assert any("Chinese" in str(m.get("memory", "")) for m in hits)


def test_forged_workspace_id_rejected(app):
    """An agent cannot address another workspace by faking parameters:
    the daemon routes purely on the registered session."""
    _register(app, "s-a", "workspace-a", "/tmp/a")
    _register(app, "s-b", "workspace-b", "/tmp/b")
    _add(app, "s-a", "secret of workspace A: API key is X9")

    # session s-b searching must not see workspace-a memory even with a
    # query crafted to find it (namespace is derived from session only)
    hits = _search(app, "s-b", "API key is X9")
    assert all("X9" not in str(m.get("memory", "")) for m in hits)


def test_unregistered_session_uses_own_cwd(app):
    _register(app, "s-a", "workspace-a", "/tmp/a")
    _add(app, "s-a", "memory belongs to workspace-a")

    hits = _search(app, "s-unknown", "memory belongs", scope="workspace")
    assert all("workspace-a" not in str(m.get("memory", "")) for m in hits)


def test_delete_all_scope_confined(app):
    _register(app, "s-a", "workspace-a", "/tmp/a")
    _add(app, "s-a", "workspace memory to delete", scope="workspace")
    _add(app, "s-a", "global memory to keep", scope="global")

    result = app.delete_all_memories(
        type("D", (), {"session_id": "s-a", "scope": "workspace"})()
    )
    assert result["scope"] == "workspace"

    remaining_global = _search(app, "s-a", "global memory", scope="global")
    assert any("global memory to keep" in str(m.get("memory", "")) for m in remaining_global)
    remaining_ws = _search(app, "s-a", "workspace memory", scope="workspace")
    assert all("workspace memory to delete" not in str(m.get("memory", "")) for m in remaining_ws)


def test_namespace_shape(app):
    g = security.global_namespace()
    w = security.workspace_namespace("proj-x")
    assert g.startswith("u:")
    assert w.startswith("u:")
    assert ":global" in g
    assert ":workspace:" in w
    assert w != g
    # deterministic
    assert security.workspace_namespace("proj-x") == w
    assert security.workspace_namespace("proj-y") != w
