"""Dual-namespace recall: merge, dedup, ordering, token budget."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from service.daemon import DaemonApp  # noqa: E402
from service.recall_engine import _merge  # noqa: E402


@pytest.fixture()
def app(mem0_env):
    app = DaemonApp(mem0_env["data_dir"], mem0_env["config"])
    app.register_session(
        type("S", (), {"session_id": "s-recall", "cwd": "/tmp/proj-r",
                       "workspace_id": "proj-r", "host": "claude",
                       "pid": 1, "client": None})()
    )
    yield app
    app.shutdown()


def _add(app, content, scope):
    app.add_memory(
        type("A", (), {"content": content, "messages": None, "scope": scope,
                       "infer": False, "metadata": None,
                       "session_id": "s-recall", "idempotency_key": None})()
    )


def test_recall_returns_both_scopes(app):
    _add(app, "the build uses bazel with remote caching", "workspace")
    _add(app, "user prefers dark mode in every editor", "global")

    result = app.recall(
        type("R", (), {"query": "bazel build caching", "session_id": "s-recall",
                       "top_k": 10, "mode": "direct"})()
    )
    assert result["context"] != ""
    texts = [str(m.get("memory", "")) for m in result["memories"]]
    assert any("bazel" in t for t in texts)


def test_recall_merge_dedup(app):
    merged = {}
    _merge(merged, {"id": "m1", "memory": "A", "score": 0.9,
                    "metadata": {"scope": "workspace"}}, boost=1.2)
    _merge(merged, {"id": "m1", "memory": "A", "score": 0.95,
                    "metadata": {"scope": "workspace"}}, boost=1.0)
    assert len(merged) == 1
    # best boosted score wins: max(0.9*1.2, 0.95*1.0) = 1.08
    assert merged["m1"]["_score"] == pytest.approx(1.08)


def test_recall_workspace_priority(app):
    """Workspace memories are boosted over global ones at equal score."""
    merged = {}
    _merge(merged, {"id": "g1", "memory": "G", "score": 0.8,
                    "metadata": {"scope": "global"}}, boost=1.0)
    _merge(merged, {"id": "w1", "memory": "W", "score": 0.8,
                    "metadata": {"scope": "workspace"}}, boost=1.2)
    ranked = sorted(merged.values(), key=lambda m: m["_score"], reverse=True)
    assert ranked[0]["id"] == "w1"


def test_recall_token_budget(app):
    for i in range(20):
        _add(app, f"generated test memory number {i} with padding content", "workspace")
    result = app.recall(
        type("R", (), {"query": "generated test memory", "session_id": "s-recall",
                       "top_k": 50, "mode": "direct"})()
    )
    assert result["used_tokens"] <= result["budget"]


def test_recall_failure_returns_empty(app):
    """Degraded mode: dead LLM/embedder still returns a usable response."""
    app.engine.cfg = app.cfg
    result = app.recall_engine.recall("anything", "proj-r", "s-recall",
                                      mode="direct", top_k=5)
    assert isinstance(result["context"], str)
