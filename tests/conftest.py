"""Shared test fixtures: fake OpenAI-compatible LLM server + daemon.

The fake server answers /chat/completions and /embeddings so the daemon
(and mem0 through litellm) never touches the network.  Embedding vectors
are deterministic per text so recall ordering is stable.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# must be set before mem0 is imported (module-level constant): no posthog
# telemetry and no shared migrations_qdrant store in tests
os.environ.setdefault("MEM0_TELEMETRY", "false")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def fake_vector(text: str, dim: int = 10) -> list[float]:
    """Deterministic bag-of-words vector: shared tokens => correlated
    vectors, so semantic search over the fake embeddings is meaningful."""
    import re as _re

    vec = [0.0] * dim
    for token in _re.findall(r"[a-z0-9_]+", text.lower()):
        idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


class FakeLLMHandler(BaseHTTPRequestHandler):
    server_version = "fake-llm/1.0"
    calls: list[Dict[str, Any]] = []

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _reply(self, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._reply({"data": [{"id": "fake-model"}]})
        else:
            self._reply({})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw or "{}")
        except json.JSONDecodeError:
            body = {}
        self.calls.append(body)

        path = self.path
        if path.rstrip("/").endswith("/chat/completions"):
            self._reply(self._chat_reply(body))
        elif path.rstrip("/").endswith("/embeddings"):
            self._reply(self._embed_reply(body))
        else:
            self._reply({})

    # ------------------------------------------------------------------
    def _chat_reply(self, body: Dict[str, Any]) -> Dict[str, Any]:
        messages = body.get("messages", [])
        system = ""
        user_text = ""
        for m in messages:
            if m.get("role") == "system":
                system = str(m.get("content", ""))
            elif m.get("role") == "user":
                user_text = str(m.get("content", ""))
        full = system + "\n" + user_text

        if "classify conversation fragments" in system:
            content = json.dumps({"scope": "workspace", "reason": "fake-classifier"})
        elif "summarize a coding session" in system:
            content = json.dumps({
                "goal": "Test goal",
                "key_decisions": ["Use fake LLM in tests"],
                "completed_work": ["Wrote tests"],
                "files_modified": ["tests/conftest.py"],
                "known_issues": [],
                "unfinished": [],
                "constraints": [],
            })
        elif "rewrite a user query" in system:
            content = json.dumps({"queries": [user_text[:100], "second angle"]})
        elif "consolidation pass" in system:
            content = json.dumps({"duplicates": [], "contradictions": []})
        elif "Memory Extractor" in system or "memory extractor" in system.lower():
            # dynamic text so mem0's hash-dedup treats each capture as new
            snippet = " ".join(user_text.split())[:60]
            content = json.dumps({
                "memory": [{"id": "0", "text": f"Extracted fact: {snippet}"}]
            })
        else:
            content = json.dumps({"memory": []})
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def _embed_reply(self, body: Dict[str, Any]) -> Dict[str, Any]:
        inputs = body.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        data = []
        for i, text in enumerate(inputs):
            vec = fake_vector(str(text))
            data.append({"index": i, "object": "embedding", "embedding": vec})
        return {"object": "list", "data": data, "model": body.get("model", "fake")}


@pytest.fixture(scope="session")
def fake_llm_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    FakeLLMHandler.calls = []
    yield f"http://127.0.0.1:{port}/v1", FakeLLMHandler.calls
    server.shutdown()
    server.server_close()


def kill_daemons_holding(data_dir: Path) -> None:
    """Kill any daemon subprocess whose command line references *data_dir*.

    Used by teardown as a safety net: a detached daemon spawned by
    ensure_daemon() survives the pytest process, and if a fixture assert
    fails before stop_daemon() runs we must not leak background Pythons.
    Windows-only scan (wmic); on POSIX fall back to the pid file.
    """
    import subprocess

    if os.name == "nt":
        try:
            for image in ("python.exe", "pythonw.exe"):
                out = subprocess.run(
                    ["wmic", "process", "where",
                     f"name='{image}' and commandline like '%{data_dir}%'",
                     "get", "processid"],
                    capture_output=True, text=True, timeout=30,
                )
                for line in out.stdout.splitlines():
                    line = line.strip()
                    if line.isdigit():
                        subprocess.run(["taskkill", "/F", "/PID", line],
                                       capture_output=True, timeout=30)
        except Exception:
            pass
    else:
        from service import bootstrap

        bootstrap.stop_daemon()


@pytest.fixture()
def mem0_env(tmp_path, fake_llm_server, monkeypatch):
    """Isolated data root + fully local config (fake LLM, mock embedder)."""
    base_url, calls = fake_llm_server
    root = tmp_path / "mem0root"
    (root / "data").mkdir(parents=True)
    monkeypatch.setenv("MEM0_LOCAL_ROOT", str(root))
    cfg = {
        "llm": {"provider": "openai", "model": "fake-model",
                "api_key": "fake-key", "base_url": base_url},
        "summary_llm": {"provider": "openai", "model": "fake-model",
                        "api_key": "fake-key", "base_url": base_url},
        "recall_llm": {"provider": "openai", "model": "fake-model",
                       "api_key": "fake-key", "base_url": base_url},
        "embedder": {"provider": "openai", "model": "fake-embed",
                     "api_key": "fake-key", "base_url": base_url,
                     "dimensions": 10},
        "auto_save": True,
        "auto_search": True,
        "search_limit": 10,
        "confidence_threshold": 0.3,
        "retention_session_days": 90,
        "recall": {"mode": "direct", "workspace_top_k": 8, "global_top_k": 4,
                   "session_top_k": 3, "max_tokens": 1600, "timeout_ms": 5000,
                   "threshold": 0.0},
        "capture": {"min_messages": 2, "min_chars": 20, "summary_min_chars": 20},
        "debug": False,
    }
    # persist so spawned (subprocess) daemons pick up the same config
    import json as _json

    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "config.json").write_text(
        _json.dumps(cfg), encoding="utf-8")
    return {
        "root": root,
        "data_dir": root / "data",
        "config": cfg,
        "base_url": base_url,
        "llm_calls": calls,
    }
