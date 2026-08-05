"""Provider factory: config passthrough, custom endpoints, mock embedder."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from service.provider_factory import (  # noqa: E402
    build_memory_config,
    chat_completion,
    extract_json_object,
)


def test_build_memory_config_pins_local_paths(mem0_env):
    cfg = mem0_env["config"]
    mem_config = build_memory_config(mem0_env["data_dir"], cfg)
    assert mem_config.vector_store.provider == "qdrant"
    assert mem_config.vector_store.config.path == str(mem0_env["data_dir"] / "qdrant")
    assert mem_config.vector_store.config.collection_name == "mem0_rvaim"
    assert mem_config.llm.provider == "openai"
    assert mem_config.llm.config["model"] == "fake-model"
    assert mem_config.llm.config["openai_base_url"] == cfg["llm"]["base_url"]
    assert mem_config.embedder.provider == "openai"
    assert mem_config.embedder.config["openai_base_url"] == cfg["embedder"]["base_url"]
    assert mem_config.vector_store.config.embedding_model_dims == 10
    assert mem_config.history_db_path == str(mem0_env["data_dir"] / "mem0-history.db")


def test_chat_completion_custom_endpoint(mem0_env):
    """The scope-classifier can talk to any OpenAI-compatible endpoint."""
    cfg = mem0_env["config"]
    reply = chat_completion(
        cfg["llm"],
        [{"role": "system", "content": "classify conversation fragments..."},
         {"role": "user", "content": "test"}],
        json_mode=True,
    )
    assert '"scope"' in reply


def test_extract_json_object():
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('prefix {"a": 1} suffix') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json_object("not json at all")


def test_env_overrides(mem0_env, monkeypatch):
    from service import config as app_config

    monkeypatch.setenv("MEM0_LLM_MODEL", "override-model")
    monkeypatch.setenv("MEM0_EMBEDDER_DIMENSIONS", "768")
    cfg = app_config.load_config()
    assert cfg["llm"]["model"] == "override-model"
    assert cfg["embedder"]["dimensions"] == 768
