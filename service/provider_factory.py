"""Build mem0 provider configs and small OpenAI-compatible chat helpers.

The daemon never hardcodes a model provider: ``llm`` and ``embedder`` are
fully independent sections of config.json (see config.py).  Supported
providers for the mem0 layer: openai (any OpenAI-compatible endpoint via
``base_url``), ollama, litellm, anthropic, etc. — anything mem0's LLM
registry understands is passed through verbatim.

For the scope-classification / summary / query-rewrite calls we talk to
the *same* endpoint using a minimal OpenAI-compatible chat client
(standard library only), so remote APIs, local servers (Ollama, LM Studio,
vLLM) and custom gateways all work.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from mem0.configs.base import MemoryConfig
from mem0.memory.main import Memory

from . import config as app_config

log = logging.getLogger("mem0-rvaim.providers")

# Embedder providers we know the output dimension of; anything else requires
# an explicit "dimensions" value in config.
_KNOWN_DIMS = {"mock": 10, "openai": None, "ollama": None}


def _llm_section(cfg: Dict[str, Any], key: str) -> Dict[str, Any]:
    section = cfg.get(key) or {}
    provider = section.get("provider") or "openai"
    model = section.get("model") or "gpt-4o-mini"
    out: Dict[str, Any] = {"provider": provider, "config": {"model": model}}
    if provider in ("openai", "litellm", "ollama"):
        out["config"]["temperature"] = 0.0
    if section.get("api_key"):
        out["config"]["api_key"] = section["api_key"]
    if section.get("base_url"):
        out["config"]["openai_base_url"] = section["base_url"]
    return out


def _embedder_section(cfg: Dict[str, Any]) -> Dict[str, Any]:
    section = cfg.get("embedder") or {}
    provider = section.get("provider") or "openai"
    model = section.get("model") or "text-embedding-3-small"
    out: Dict[str, Any] = {"provider": provider, "config": {"model": model}}
    if section.get("api_key"):
        out["config"]["api_key"] = section["api_key"]
    if section.get("base_url"):
        out["config"]["openai_base_url"] = section["base_url"]
    return out


def build_memory_config(data_dir: Any, cfg: Optional[Dict[str, Any]] = None) -> MemoryConfig:
    """Build a mem0 MemoryConfig pinned to local Qdrant + SQLite paths."""
    cfg = cfg or app_config.load_config()
    embedder = _embedder_section(cfg)
    provider = embedder["provider"]
    dims = (cfg.get("embedder") or {}).get("dimensions")
    if dims is None and provider in ("openai", "ollama"):
        dims = 1536  # common default; users must adjust for other models
    elif dims is None:
        dims = _KNOWN_DIMS.get(provider, 1536)

    vector_store = {
        "provider": "qdrant",
        "config": {
            "collection_name": "mem0_rvaim",
            "path": str(data_dir / "qdrant"),
            "on_disk": True,
            "embedding_model_dims": int(dims),
        },
    }

    return MemoryConfig(
        vector_store=vector_store,
        llm=_llm_section(cfg, "llm"),
        embedder=embedder,
        history_db_path=str(data_dir / "mem0-history.db"),
        version="v1.1",
    )


def create_memory(data_dir: Any, cfg: Optional[Dict[str, Any]] = None) -> Memory:
    """Create a mem0 Memory instance (single instance per daemon)."""
    mem_config = build_memory_config(data_dir, cfg)
    # MemoryConfig -> dict, then let from_config re-validate (mem0 expects a mapping)
    return Memory.from_config(mem_config.model_dump())


def chat_completion(
    llm_cfg: Dict[str, Any],
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    timeout: float = 60.0,
    json_mode: bool = False,
) -> str:
    """Minimal OpenAI-compatible chat completion using standard library.

    Supports any provider that exposes an OpenAI-compatible endpoint via
    ``base_url`` (OpenAI, Ollama, LM Studio, vLLM, proxies...).  Raises
    RuntimeError on transport or API errors.
    """
    provider = llm_cfg.get("provider", "openai")
    if provider not in ("openai", "litellm", "ollama", "vllm", "lmstudio", "deepseek", "xai", "groq", "together", "openai_structured", "custom"):
        raise RuntimeError(f"chat_completion does not support provider: {provider}")
    base_url = (llm_cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    api_key = llm_cfg.get("api_key") or ""
    model = llm_cfg.get("model") or "gpt-4o-mini"
    url = f"{base_url}/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"LLM response invalid: {exc}") from exc
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"LLM response missing content: {data}") from exc


def extract_json_object(text: str) -> Dict[str, Any]:
    """Best-effort parse of a JSON object from an LLM reply."""
    import re

    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise ValueError(f"Could not parse JSON from LLM reply: {text[:200]}")
