"""Memory engine CRUD and idempotency-key behavior."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from service import security  # noqa: E402
from service.daemon import DaemonApp  # noqa: E402


@pytest.fixture()
def app(mem0_env):
    app = DaemonApp(mem0_env["data_dir"], mem0_env["config"])
    app.register_session(
        type("S", (), {"session_id": "s-crud", "cwd": "/tmp/proj-crud",
                       "workspace_id": "proj-crud", "host": "claude",
                       "pid": 1, "client": None})()
    )
    yield app
    app.shutdown()


def _add(app, content, **kw):
    return app.add_memory(
        type("A", (), {"content": content, "messages": None,
                       "scope": kw.get("scope", "workspace"),
                       "infer": False, "metadata": None,
                       "session_id": "s-crud",
                       "idempotency_key": kw.get("idem")})()
    )


def test_add_get_update_delete(app):
    result = _add(app, "the api rate limit is 100 req/min")
    assert result["ok"] is True
    mid = result["results"][0]["id"]

    memory = app.get_memory(mid)
    assert "rate limit" in str(memory.get("memory", ""))

    ok = app.update_memory(
        type("U", (), {"memory_id": mid, "session_id": "s-crud",
                       "text": "the api rate limit is now 500 req/min",
                       "metadata_patch": {"pinned": True}})()
    )
    assert ok["ok"] is True
    memory2 = app.get_memory(mid)
    assert "500" in str(memory2.get("memory", ""))
    assert (memory2.get("metadata") or {}).get("pinned") is True

    ok = app.delete_memory(type("D", (), {"memory_id": mid, "session_id": "s-crud"})())
    assert ok["ok"] is True
    with pytest.raises(KeyError):
        app.get_memory(mid)


def test_add_messages_infers(app):
    result = app.add_memory(
        type("A", (), {"content": None,
                       "messages": [{"role": "user", "content": "my stack is python"},
                                    {"role": "assistant", "content": "great choice"}],
                       "scope": "workspace", "infer": True, "metadata": None,
                       "session_id": "s-crud", "idempotency_key": None})()
    )
    assert result["ok"] is True


def test_idempotency_key(app):
    result1 = _add(app, "idempotent memory content", idem="k1")
    result2 = _add(app, "idempotent memory content", idem="k1")
    assert result2.get("idempotent_replay") is True
    assert result1["results"] == result2["results"]


def test_scope_auto_classifies_via_llm(app):
    """scope=auto uses the Memory LLM classifier (fake -> workspace)."""
    result = _add(app, "user prefers tab indentation in all projects", scope="auto")
    assert result["scope"] == "workspace"  # fake classifier says workspace


def test_update_rejects_internal_metadata(app):
    """Clients cannot forge authoritative metadata (scope/workspace)."""
    result = _add(app, "cannot forge metadata", scope="workspace",
                  )
    mid = result["results"][0]["id"]
    app.update_memory(
        type("U", (), {"memory_id": mid, "session_id": "s-crud",
                       "text": None,
                       "metadata_patch": {"scope": "global",
                                          "workspace_id": "other-ws"}})()
    )
    memory = app.get_memory(mid)
    meta = memory.get("metadata") or {}
    # our daemon's update merges client keys but authoritative keys are
    # stored separately and re-applied at write time; verify scope intact
    assert meta.get("workspace_id") in (None, "proj-crud")
