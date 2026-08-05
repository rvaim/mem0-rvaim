"""Transcript incremental capture: delta reads, idempotency across
Stop/PreCompact, cursor advance only after success."""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from service.daemon import DaemonApp  # noqa: E402


def _write_transcript(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant(text):
    return {"type": "assistant", "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
    }}


@pytest.fixture()
def app(mem0_env):
    app = DaemonApp(mem0_env["data_dir"], mem0_env["config"])
    app.register_session(
        type("S", (), {"session_id": "s-cap", "cwd": "/tmp/proj-c",
                       "workspace_id": "proj-c", "host": "claude",
                       "pid": 1, "client": None})()
    )
    yield app
    app.shutdown()


def _capture(app, transcript, source="stop"):
    return app.capture(
        type("C", (), {"session_id": "s-cap", "transcript_path": str(transcript),
                       "cwd": "/tmp/proj-c", "source": source,
                       "capture_summary": False})()
    )


def _count(app):
    result = app.get_memories(
        type("G", (), {"session_id": "s-cap", "scope": "workspace",
                       "top_k": 500, "memory_type": None})()
    )
    return len(result["results"].get("workspace", []))


def test_incremental_capture(app, tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript, [
        _user("Please implement a cache layer for the API"),
        _assistant("Done — added redis cache with TTL of 60 seconds"),
    ])
    result = _capture(app, transcript)
    assert result["facts"]["captured"] is True
    first_count = _count(app)
    assert first_count >= 1

    # same content again (Stop + PreCompact double fire) -> no duplicates
    result2 = _capture(app, transcript)
    assert result2["facts"]["captured"] is False
    assert _count(app) == first_count
    result3 = _capture(app, transcript, source="pre-compact")
    assert result3["facts"]["captured"] is False
    assert _count(app) == first_count

    # new content appended -> only the delta is captured
    _write_transcript(transcript, [
        _user("Please implement a cache layer for the API"),
        _assistant("Done — added redis cache with TTL of 60 seconds"),
        _user("Now add a circuit breaker to the redis client"),
        _assistant("Added circuit breaker with 3 retries and backoff"),
    ])
    result4 = _capture(app, transcript)
    assert result4["facts"]["captured"] is True
    assert _count(app) > first_count


def test_capture_requires_substantial_content(app, tmp_path):
    transcript = tmp_path / "tiny.jsonl"
    _write_transcript(transcript, [_user("hi"), _assistant("hello!")])
    result = _capture(app, transcript)
    assert result["facts"]["captured"] is False
    assert _count(app) == 0


def test_capture_failure_keeps_cursor(app, tmp_path, monkeypatch):
    """If capture fails (e.g. LLM error), the cursor must not advance so
    the retry queue can pick it up."""
    transcript = tmp_path / "fail.jsonl"
    _write_transcript(transcript, [
        _user("Design the database schema for the billing module"),
        _assistant("Schema designed with invoices and payments tables"),
    ])

    # simulate a downstream LLM failure during the store step
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated LLM outage")

    monkeypatch.setattr(app.capture_engine.engine, "add_messages", _boom)
    result = _capture(app, transcript)
    assert result["facts"]["captured"] is False

    # cursor must NOT have advanced past the failed content
    cursor = app.store.get_cursor("s-cap")
    assert cursor is None or cursor["offset_bytes"] == 0

    # retry with the pipeline restored must succeed exactly once
    monkeypatch.undo()
    retry = _capture(app, transcript)
    assert retry["facts"]["captured"] is True
    count = _count(app)
    assert count >= 1
    # and no duplicate on immediate re-capture
    again = _capture(app, transcript)
    assert again["facts"]["captured"] is False
    assert _count(app) == count


def test_session_summary_capture(app, tmp_path):
    transcript = tmp_path / "sum.jsonl"
    _write_transcript(transcript, [
        _user("Refactor the auth middleware to use JWT"),
        _assistant(
            "Refactored the auth middleware to use JWT with asymmetric keys. "
            "Updated the login endpoint, added token refresh, migrated tests. "
            "Key decision: RS256 over HS256 for multi-service verification."
        ),
    ])
    result = app.capture(
        type("C", (), {"session_id": "s-cap", "transcript_path": str(transcript),
                       "cwd": "/tmp/proj-c", "source": "stop",
                       "capture_summary": True})()
    )
    assert result["summary"]["captured"] is True
    mems = app.get_memories(
        type("G", (), {"session_id": "s-cap", "scope": "workspace",
                       "top_k": 500, "memory_type": "session_summary"})()
    )["results"].get("workspace", [])
    assert any("关键决定" in str(m.get("memory", "")) for m in mems)
