"""Local security helpers: auth token, namespace generation, hashing.

The daemon only listens on loopback, but every request must still carry a
random bearer token so that other local processes / browser pages cannot
read or write memory.  The token file is created with owner-only
permissions (best effort on Windows, enforced on POSIX).
"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from . import config


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def token_path() -> Path:
    return config.root_dir() / "runtime" / "daemon.token"


def write_token(token: str) -> Path:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(token)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # Windows: chmod is a no-op, ACLs are the user's responsibility
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def read_token() -> str:
    try:
        return token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def constant_time_equal(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def user_hash() -> str:
    """Stable per-machine/user hash (hex)."""
    return config.load_identity().get("user_hash", "unknown")


def workspace_hash(workspace_id: str) -> str:
    """Deterministic 16-hex hash of a workspace id."""
    return hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:16]


def global_namespace() -> str:
    """Internal namespace for cross-workspace (global) memories."""
    return f"u:{user_hash()}:global"


def workspace_namespace(workspace_id: str) -> str:
    """Internal namespace for one workspace's memories.

    Generated server-side; agents can never submit or override it.
    """
    return f"u:{user_hash()}:workspace:{workspace_hash(workspace_id)}"


def is_internal_namespace(value: str) -> bool:
    """True if *value* looks like one of our namespaces (defense in depth)."""
    return value.startswith(f"u:{user_hash()}:")


def scrub_secrets(text: str) -> str:
    """Redact bearer tokens / API keys before logging."""
    for secret in (read_token(),):
        if secret and secret in text:
            text = text.replace(secret, "[REDACTED]")
    for env_name in ("MEM0_LLM_API_KEY", "MEM0_EMBEDDER_API_KEY",
                     "MEM0_SUMMARY_LLM_API_KEY", "MEM0_RECALL_LLM_API_KEY",
                     "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        value = os.environ.get(env_name)
        if value and value in text:
            text = text.replace(value, "[REDACTED]")
    return text
