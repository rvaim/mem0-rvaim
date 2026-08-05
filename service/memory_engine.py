"""Mem0 wrapper: the only component allowed to touch Qdrant / history DB.

The daemon owns a single ``Memory`` instance.  All namespaces are passed
as mem0 ``user_id`` — internally generated, never client-supplied.  The
persistence directory is fixed to the data dir; mem0's default temp Qdrant
path is never used.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from . import config as app_config
from .provider_factory import create_memory

log = logging.getLogger("mem0-rvaim.memory")


class MemoryEngine:
    def __init__(self, data_dir: Any, cfg: Optional[Dict[str, Any]] = None):
        self.data_dir = data_dir
        self.cfg = cfg or app_config.load_config()
        # mem0's SQLiteManager (history.db) is not thread-safe; serialize
        # all writes so concurrent HTTP requests / retry worker never race
        self._write_lock = threading.Lock()
        self.memory = None
        try:
            self.memory = create_memory(self.data_dir, self.cfg)
            log.info("memory engine ready (qdrant=%s)", self.data_dir / "qdrant")
        except Exception as exc:
            # daemon must still start without provider credentials; every
            # operation degrades to empty results until config is fixed
            log.error("memory engine unavailable: %s (configure llm/embedder)", exc)

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def add(
        self,
        namespace: str,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        infer: bool = True,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._write_lock:
            results = self.memory.add(
                content,
                user_id=namespace,
                metadata=metadata,
                infer=infer,
                run_id=run_id,
            )
        return results.get("results", [])

    def add_messages(
        self,
        namespace: str,
        messages: List[Dict[str, str]],
        *,
        metadata: Optional[Dict[str, Any]] = None,
        infer: bool = True,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._write_lock:
            results = self.memory.add(
                messages,
                user_id=namespace,
                metadata=metadata,
                infer=infer,
                run_id=run_id,
            )
        return results.get("results", [])

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 8,
        threshold: float = 0.3,
    ) -> List[Dict[str, Any]]:
        try:
            results = self.memory.search(
                query,
                filters={"user_id": namespace},
                top_k=top_k,
                threshold=threshold,
            )
        except Exception as exc:
            log.warning("search failed: %s", exc)
            return []
        return results.get("results", [])

    def get_all(
        self,
        namespace: str,
        top_k: int = 100,
        memory_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List memories, optionally filtered by metadata.memory_type.

        The filter is applied in-process: mem0's dotted metadata filter
        does not reliably reach the Qdrant payload query, and our stores
        are small enough that this is not a bottleneck.
        """
        try:
            results = self.memory.get_all(
                filters={"user_id": namespace}, top_k=top_k
            )
        except Exception as exc:
            log.warning("get_all failed: %s", exc)
            return []
        items = results.get("results", [])
        if memory_type:
            items = [
                m for m in items
                if (m.get("metadata") or {}).get("memory_type") == memory_type
            ]
        return items

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.memory.get(memory_id)
        except Exception as exc:
            log.warning("get(%s) failed: %s", memory_id, exc)
            return None

    # ------------------------------------------------------------------
    # updates / deletes
    # ------------------------------------------------------------------
    def update(self, memory_id: str, text: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None) -> bool:
        try:
            with self._write_lock:
                self.memory.update(memory_id, text=text, metadata=metadata)
            return True
        except Exception as exc:
            log.warning("update(%s) failed: %s", memory_id, exc)
            return False

    def delete(self, memory_id: str) -> bool:
        try:
            with self._write_lock:
                self.memory.delete(memory_id)
            return True
        except Exception as exc:
            log.warning("delete(%s) failed: %s", memory_id, exc)
            return False

    def delete_all(self, namespace: str) -> int:
        try:
            with self._write_lock:
                result = self.memory.delete_all(user_id=namespace)
            return int(result.get("deleted_count", result.get("deleted", 0) or 0))
        except Exception as exc:
            log.warning("delete_all(%s) failed: %s", namespace, exc)
            return 0

    @property
    def available(self) -> bool:
        """False when providers are unconfigured (degraded mode)."""
        return self.memory is not None

    def close(self) -> None:
        """Release Qdrant/SQLite handles (required before the daemon exits
        so a restarted daemon can re-open the same data dir)."""
        if self.memory is None:
            return
        try:
            self.memory.close()
        except Exception as exc:
            log.warning("memory close failed: %s", exc)
        self.memory = None

    # ------------------------------------------------------------------
    # ownership checks (defense in depth)
    # ------------------------------------------------------------------
    def owned_by(self, memory_id: str, namespace: str) -> bool:
        """True if the memory belongs to *namespace* (used before delete)."""
        memory = self.get(memory_id)
        if not memory:
            return False
        meta = memory.get("metadata") or {}
        stored_user = meta.get("user_id")
        # some mem0 versions keep user_id top-level
        if stored_user is None:
            stored_user = memory.get("user_id")
        return stored_user == namespace

    def count(self, namespace: str) -> int:
        try:
            return len(self.get_all(namespace, top_k=500))
        except Exception:
            return 0
