"""Resolve the stable local user identity (no cloud key).

The identity is a random hash created on first run in
``~/.mem0/local/config/identity.json`` — see service/config.load_identity.
This module is the scripts-side mirror used by hook scripts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def identity_path() -> Path:
    root = Path(os.environ.get("MEM0_LOCAL_ROOT") or Path.home() / ".mem0" / "local")
    return root / "config" / "identity.json"


def resolve_user_hash() -> str:
    """Stable local user hash (hex)."""
    path = identity_path()
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as fh:
                identity = json.load(fh)
            if identity.get("user_hash"):
                return identity["user_hash"]
        except (OSError, json.JSONDecodeError):
            pass
    import uuid

    user_hash = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"user_hash": user_hash}, fh, indent=2)
    except OSError:
        pass
    return user_hash


def resolve_user_id() -> str:
    """Display user id: env override or OS user name (never a cloud key)."""
    explicit = os.environ.get("MEM0_USER_ID", "").strip()
    if explicit:
        return explicit
    return os.environ.get("USER") or os.environ.get("USERNAME") or "default"


def resolve_config() -> dict:
    try:
        from load_settings import load_settings

        return load_settings()
    except ImportError:
        return {
            "auto_save": True,
            "auto_search": True,
            "search_limit": 10,
            "retention_session_days": 90,
            "confidence_threshold": 0.3,
            "debug": False,
        }
