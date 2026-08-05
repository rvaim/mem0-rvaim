"""Concurrency: multiple simulated clients writing/reading simultaneously."""

from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from service.daemon import DaemonApp  # noqa: E402


def test_concurrent_adds_are_safe(mem0_env):
    app = DaemonApp(mem0_env["data_dir"], mem0_env["config"])
    try:
        app.register_session(
            type("S", (), {"session_id": "s-conc", "cwd": "/tmp/proj-z",
                           "workspace_id": "proj-z", "host": "claude",
                           "pid": 1, "client": None})()
        )
        errors = []

        def writer(i):
            try:
                for j in range(5):
                    app.add_memory(
                        type("A", (), {
                            "content": f"concurrent memory {i}-{j}",
                            "messages": None, "scope": "workspace",
                            "infer": False, "metadata": None,
                            "session_id": "s-conc", "idempotency_key": None})()
                    )
                    app.search_memories(
                        type("S", (), {"query": f"concurrent memory {i}-{j}",
                                       "session_id": "s-conc", "scope": "workspace",
                                       "top_k": 5, "threshold": 0.0,
                                       "memory_type": None})()
                    )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors, f"concurrent failures: {errors}"

        result = app.get_memories(
            type("G", (), {"session_id": "s-conc", "scope": "workspace",
                           "top_k": 500, "memory_type": None})()
        )
        assert len(result["results"]["workspace"]) == 20
    finally:
        app.shutdown()
