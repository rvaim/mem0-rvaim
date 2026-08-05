"""Paths, defaults and config loading for the mem0-rvaim service.

Directory layout (default root ``~/.mem0/local``):

    <root>/
    ├── venv/            Python virtualenv created by ensure_deps.sh
    ├── config/          config.json + identity.json (non-secret)
    ├── data/            qdrant/, mem0-history.db, state.db
    ├── runtime/         daemon.pid / daemon.port / daemon.token / daemon.lock
    ├── logs/            daemon.log (API keys never logged)
    └── backups/         export destination

The root can be overridden with ``MEM0_LOCAL_ROOT`` (used by tests).
Program directory (the plugin install) and data directory are separate:
plugin upgrades never touch ``data/``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

SERVICE_VERSION = "0.3.0"

DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), ".mem0", "local")

# Environment variable overrides for model configuration.
_ENV_MAP = {
    "llm": {
        "provider": "MEM0_LLM_PROVIDER",
        "model": "MEM0_LLM_MODEL",
        "api_key": "MEM0_LLM_API_KEY",
        "base_url": "MEM0_LLM_BASE_URL",
    },
    "summary_llm": {
        "provider": "MEM0_SUMMARY_LLM_PROVIDER",
        "model": "MEM0_SUMMARY_LLM_MODEL",
        "api_key": "MEM0_SUMMARY_LLM_API_KEY",
        "base_url": "MEM0_SUMMARY_LLM_BASE_URL",
    },
    "recall_llm": {
        "provider": "MEM0_RECALL_LLM_PROVIDER",
        "model": "MEM0_RECALL_LLM_MODEL",
        "api_key": "MEM0_RECALL_LLM_API_KEY",
        "base_url": "MEM0_RECALL_LLM_BASE_URL",
    },
    "embedder": {
        "provider": "MEM0_EMBEDDER_PROVIDER",
        "model": "MEM0_EMBEDDER_MODEL",
        "api_key": "MEM0_EMBEDDER_API_KEY",
        "base_url": "MEM0_EMBEDDER_BASE_URL",
        "dimensions": "MEM0_EMBEDDER_DIMENSIONS",
    },
}

DEFAULTS: Dict[str, Any] = {
    "llm": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
    },
    "summary_llm": None,  # falls back to llm
    "recall_llm": None,   # falls back to llm
    "embedder": {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "dimensions": 1536,
    },
    "auto_save": True,
    "auto_search": True,
    "search_limit": 10,
    "confidence_threshold": 0.3,
    "retention_session_days": 90,
    "recall": {
        "mode": "direct",          # direct | rewrite
        "workspace_top_k": 8,
        "global_top_k": 4,
        "session_top_k": 3,
        "max_tokens": 1600,
        "timeout_ms": 5000,
        "threshold": 0.3,
    },
    "capture": {
        "min_messages": 2,
        "min_chars": 100,
        "summary_min_chars": 300,
    },
    "debug": False,
}


def root_dir() -> Path:
    return Path(os.environ.get("MEM0_LOCAL_ROOT") or DEFAULT_ROOT)


def ensure_root() -> Path:
    root = root_dir()
    for sub in ("config", "data", "runtime", "logs", "backups"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def config_path() -> Path:
    return root_dir() / "config" / "config.json"


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> Dict[str, Any]:
    """Load config.json merged over defaults, then apply env-var overrides."""
    config = json.loads(json.dumps(DEFAULTS))  # deep copy
    path = config_path()
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                config = _deep_merge(config, json.load(fh))
        except (OSError, json.JSONDecodeError):
            pass

    for section, mapping in _ENV_MAP.items():
        base = config.get(section) or {}
        if not isinstance(base, dict):
            base = {}
        changed = False
        for field, env_name in mapping.items():
            value = os.environ.get(env_name)
            if value is not None and value != "":
                if field == "dimensions":
                    try:
                        base[field] = int(value)
                    except ValueError:
                        continue
                else:
                    base[field] = value
                changed = True
        if changed:
            config[section] = base

    # summary_llm / recall_llm inherit the main llm when not configured
    for section in ("summary_llm", "recall_llm"):
        if not config.get(section):
            config[section] = dict(config.get("llm") or {})
    return config


def save_config(config: Dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_identity() -> Dict[str, Any]:
    """Load or create the stable local identity (machine/user level).

    ``user_hash`` is a random UUID created on first run; it is the base of
    every internal namespace, so memory never crosses machines/users even
    if the data directory is copied.  No cloud key involved.
    """
    path = root_dir() / "config" / "identity.json"
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                identity = json.load(fh)
            if identity.get("user_hash"):
                return identity
        except (OSError, json.JSONDecodeError):
            pass
    import uuid

    identity = {"user_hash": uuid.uuid4().hex, "created_at": None}
    try:
        from datetime import datetime, timezone

        identity["created_at"] = datetime.now(timezone.utc).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(identity, fh, indent=2)
    except OSError:
        pass
    return identity
