"""Local Memory Daemon: loopback HTTP API owned by the plugin.

Responsibilities:
    * owns the single mem0 Memory instance + Qdrant Local + SQLite
    * exposes health / recall / capture / memory CRUD / stats / admin API
    * enforces bearer-token auth and session->workspace routing
    * graceful shutdown + retry worker for failed background captures

Only this process may open Qdrant or SQLite.  Never binds to anything but
127.0.0.1; tokens are random and regenerated on restart.
"""

if __name__ == "__main__" and __package__ in (None, ""):
    # allow direct execution from anywhere (relative imports need package mode)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    import service.daemon as _entry

    raise SystemExit(_entry.main())

import json
import logging
import os
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from . import config as app_config
from . import security
from .capture_engine import CaptureEngine
from .memory_engine import MemoryEngine
from .provider_factory import chat_completion
from .recall_engine import RecallEngine
from .schemas import (
    AddMemoryRequest,
    CaptureRequest,
    ConsolidateRequest,
    DeleteAllMemoriesRequest,
    DeleteEntitiesRequest,
    DeleteMemoryRequest,
    ExportRequest,
    GetMemoriesRequest,
    ImportRequest,
    ListEntitiesRequest,
    RecallRequest,
    ReloadRequest,
    SearchMemoriesRequest,
    SessionRegisterRequest,
    UpdateMemoryRequest,
)
from .scope_router import (
    SCOPE_GLOBAL,
    SCOPE_WORKSPACE,
    build_metadata,
    namespace_for,
    resolve_scope_for_request,
)
from .state_store import StateStore

log = logging.getLogger("mem0-rvaim.daemon")

MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB request cap
RETRY_WORKER_INTERVAL = 60.0


class DaemonApp:
    """Business logic holder; routes are dispatched by the HTTP handler."""

    def __init__(self, data_dir: Path, cfg: Optional[Dict[str, Any]] = None):
        self.data_dir = data_dir
        self.cfg = cfg or app_config.load_config()
        self.store = StateStore(data_dir / "state.db")
        self.engine = MemoryEngine(data_dir, self.cfg)
        self.capture_engine = CaptureEngine(self.store, self.engine, self.cfg)
        self.recall_engine = RecallEngine(self.engine, self.cfg)
        self._stop = threading.Event()
        self._retry_thread = threading.Thread(
            target=self._retry_loop, name="retry-worker", daemon=True
        )
        self._retry_thread.start()
        log.info("daemon app ready (data_dir=%s)", data_dir)

    # ------------------------------------------------------------------
    # session / workspace
    # ------------------------------------------------------------------
    def register_session(self, body: SessionRegisterRequest) -> Dict[str, Any]:
        cwd = body.cwd
        from . import scope_router

        workspace_id = body.workspace_id or scope_router.fallback_workspace_id(cwd)
        self.store.register_session(
            session_id=body.session_id,
            workspace_id=workspace_id,
            cwd=cwd,
            host=body.host or "unknown",
            pid=body.pid,
            workspace_name=workspace_id,
        )
        return {
            "ok": True,
            "session_id": body.session_id,
            "workspace_id": workspace_id,
            "namespace": security.workspace_namespace(workspace_id),
        }

    def _workspace_for(self, session_id: str, cwd: Optional[str] = None) -> str:
        ws = self.store.workspace_for_session(session_id)
        if ws:
            return ws
        if cwd:
            from . import scope_router

            return scope_router.fallback_workspace_id(cwd)
        return "unknown"

    # ------------------------------------------------------------------
    # recall / capture
    # ------------------------------------------------------------------
    def recall(self, body: RecallRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id)
        result = self.recall_engine.recall(
            body.query, workspace_id, body.session_id, mode=body.mode, top_k=body.top_k
        )
        result["workspace_id"] = workspace_id
        return result

    def capture(self, body: CaptureRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id, body.cwd)
        transcript_path = body.transcript_path or ""
        if not transcript_path:
            return {"ok": False, "reason": "no-transcript-path", "facts": 0}
        facts = self.capture_engine.capture_facts(
            body.session_id, workspace_id, transcript_path, source=body.source or "stop"
        )
        summary = {"captured": False, "reason": "not-requested"}
        if body.capture_summary and facts.get("captured"):
            summary = self.capture_engine.capture_summary(
                body.session_id, workspace_id, transcript_path, source=body.source or "stop"
            )
        return {
            "ok": True,
            "workspace_id": workspace_id,
            "facts": facts,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # memory CRUD (scope resolved server-side)
    # ------------------------------------------------------------------
    def add_memory(self, body: AddMemoryRequest) -> Dict[str, Any]:
        if body.content is None and body.messages is None:
            raise ValueError("content or messages required")
        if body.messages is not None and not body.messages:
            raise ValueError("messages must not be empty")

        idem = body.idempotency_key or ""
        if idem:
            done = self.store.get_state(f"idem:{body.session_id}:{idem}")
            if done:
                return {"ok": True, "idempotent_replay": True, **done}

        workspace_id = self._workspace_for(body.session_id or "default-session")
        text = body.content or ""
        if not text and body.messages:
            text = "\n".join(
                f"{m['role']}: {str(m.get('content', ''))[:2000]}" for m in body.messages
            )[:6000]

        scope = body.scope or "auto"
        scope, reason = resolve_scope_for_request(scope, workspace_id, text, self.cfg)
        if scope == "discard":
            return {"ok": True, "discarded": True, "scope": "discard", "reason": reason}

        namespace = namespace_for(workspace_id, scope, None)
        metadata = build_metadata(
            scope=scope,
            workspace_id=workspace_id if scope == SCOPE_WORKSPACE else "",
            session_id=body.session_id or "manual",
            memory_type="fact",
            source="manual",
            extra=body.metadata,
        )
        infer = body.infer
        if infer is None:
            infer = body.messages is not None  # message-based writes infer; content writes don't

        if body.messages:
            results = self.engine.add_messages(
                namespace, body.messages, metadata=metadata, infer=infer
            )
        else:
            results = self.engine.add(
                namespace, body.content or "", metadata=metadata, infer=infer
            )
        if idem:
            self.store.set_state(
                f"idem:{body.session_id}:{idem}", {"scope": scope, "results": results}
            )
        return {"ok": True, "scope": scope, "results": results}

    def search_memories(self, body: SearchMemoriesRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id)
        results: Dict[str, Any] = {}
        threshold = body.threshold if body.threshold is not None else float(
            (self.cfg.get("recall") or {}).get("threshold", 0.3)
        )
        top_k = body.top_k or 10
        if body.scope in ("workspace", "both"):
            results["workspace"] = self.engine.search(
                namespace_for(workspace_id, SCOPE_WORKSPACE, None),
                body.query, top_k=top_k, threshold=threshold,
            )
        if body.scope in ("global", "both"):
            results["global"] = self.engine.search(
                namespace_for(workspace_id, SCOPE_GLOBAL, None),
                body.query, top_k=top_k, threshold=threshold,
            )
        return {"workspace_id": workspace_id, "results": results}

    def get_memories(self, body: GetMemoriesRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id)
        results: Dict[str, Any] = {}
        top_k = body.top_k or 100
        if body.scope in ("workspace", "both"):
            results["workspace"] = self.engine.get_all(
                namespace_for(workspace_id, SCOPE_WORKSPACE, None),
                top_k=top_k, memory_type=body.memory_type,
            )
        if body.scope in ("global", "both"):
            results["global"] = self.engine.get_all(
                namespace_for(workspace_id, SCOPE_GLOBAL, None),
                top_k=top_k, memory_type=body.memory_type,
            )
        return {"workspace_id": workspace_id, "results": results}

    def get_memory(self, memory_id: str) -> Dict[str, Any]:
        memory = self.engine.get(memory_id)
        if memory is None:
            raise KeyError(f"memory not found: {memory_id}")
        return memory

    # keys clients may never overwrite via metadata_patch
    _AUTHORITATIVE_META_KEYS = (
        "actual_user_id", "scope", "workspace_id", "workspace_name",
        "session_id", "memory_type", "source",
    )

    def update_memory(self, body: UpdateMemoryRequest) -> Dict[str, Any]:
        meta = dict(body.metadata_patch or {})
        for key in self._AUTHORITATIVE_META_KEYS:
            meta.pop(key, None)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        meta["updated_at"] = now
        ok = self.engine.update(body.memory_id, text=body.text, metadata=meta)
        if not ok:
            raise KeyError(f"memory not found or update failed: {body.memory_id}")
        return {"ok": True}

    def delete_memory(self, body: DeleteMemoryRequest) -> Dict[str, Any]:
        ok = self.engine.delete(body.memory_id)
        if not ok:
            raise KeyError(f"memory not found: {body.memory_id}")
        return {"ok": True}

    def delete_all_memories(self, body: DeleteAllMemoriesRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id)
        if body.scope == SCOPE_GLOBAL:
            namespace = security.global_namespace()
        else:
            namespace = security.workspace_namespace(workspace_id)
        deleted = self.engine.delete_all(namespace)
        return {"ok": True, "scope": body.scope, "deleted": deleted}

    def list_workspaces(self) -> Dict[str, Any]:
        return {"workspaces": self.store.list_workspaces()}

    def list_entities(self, body: ListEntitiesRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id)
        namespace = security.workspace_namespace(workspace_id)
        try:
            entities = self.engine.memory.entity_store.list_entities(
                user_id=namespace, entity_type=body.entity_type, limit=body.limit or 100
            )
            return {"entities": entities}
        except Exception as exc:
            log.warning("list_entities failed: %s", exc)
            return {"entities": []}

    def delete_entities(self, body: DeleteEntitiesRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id)
        namespace = security.workspace_namespace(workspace_id)
        try:
            result = self.engine.memory.entity_store.delete_entities(
                entity_names=body.entity_names, user_id=namespace,
                entity_type=body.entity_type,
            )
            return {"ok": True, "deleted": result}
        except Exception as exc:
            log.warning("delete_entities failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # stats / health / admin
    # ------------------------------------------------------------------
    def stats(self, session_id: Optional[str]) -> Dict[str, Any]:
        workspace_id = self._workspace_for(session_id or "")
        namespaces = {
            "global": security.global_namespace(),
            "workspace": security.workspace_namespace(workspace_id),
        }
        counts = {k: self.engine.count(v) for k, v in namespaces.items()}
        sessions = self.store._query_one("SELECT COUNT(*) AS c FROM sessions")
        retry_count = self.store._query_one("SELECT COUNT(*) AS c FROM retry_tasks")
        return {
            "workspace_id": workspace_id,
            "memory_counts": counts,
            "sessions_known": sessions["c"] if sessions else 0,
            "pending_retries": retry_count["c"] if retry_count else 0,
            "daemon_version": app_config.SERVICE_VERSION,
        }

    def health(self) -> Dict[str, Any]:
        degraded: list[str] = []
        if not self.engine.available:
            degraded.append("memory-engine:provider-unconfigured")
        else:
            try:
                self.engine.memory.search(
                    "probe", filters={"user_id": security.global_namespace()},
                    top_k=1, threshold=0.0,
                )
            except Exception as exc:
                degraded.append(f"vector-store:{exc}")
        return {
            "ok": True,
            "version": app_config.SERVICE_VERSION,
            "degraded": degraded,
            "pid": os.getpid(),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }

    def reload_config(self) -> Dict[str, Any]:
        self.cfg = app_config.load_config()
        self.recall_engine.cfg = self.cfg
        self.capture_engine.cfg = self.cfg
        return {"ok": True, "config_loaded": True}

    # ------------------------------------------------------------------
    # events (async compatibility shim)
    # ------------------------------------------------------------------
    def event_status(self, event_id: str) -> Dict[str, Any]:
        return {
            "event_id": event_id,
            "status": "SUCCEEDED",
            "result": {"memory_ids": [event_id]},
        }

    # ------------------------------------------------------------------
    # consolidate (dream) — Memory LLM based analysis
    # ------------------------------------------------------------------
    def consolidate(self, body: ConsolidateRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id)
        namespace = security.workspace_namespace(workspace_id)
        memories = self.engine.get_all(namespace, top_k=200)
        if len(memories) < 2:
            return {"ok": True, "dry_run": body.dry_run, "duplicates": [], "contradictions": []}

        llm_cfg = dict(self.cfg.get("llm") or {})
        if not llm_cfg.get("api_key") and "openai.com" in (llm_cfg.get("base_url") or ""):
            return {"ok": True, "dry_run": body.dry_run, "error": "llm-unconfigured",
                    "duplicates": [], "contradictions": []}
        from .provider_factory import extract_json_object

        items = "\n".join(
            f"{i}: {str(m.get('memory', ''))[:300]}" for i, m in enumerate(memories[:60])
        )
        system = (
            "You analyze memories for a consolidation pass. Respond with JSON only:\n"
            '{"duplicates": [{"ids": [0, 3], "merged_text": "..."}], '
            '"contradictions": [{"ids": [1, 5], "note": "..."}]}\n'
            "ids refer to the numbered list. Only report true duplicates/contradictions."
        )
        try:
            reply = chat_completion(
                llm_cfg,
                [{"role": "system", "content": system},
                 {"role": "user", "content": items}],
                json_mode=True, timeout=60.0,
            )
            parsed = extract_json_object(reply)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        duplicates = []
        for d in parsed.get("duplicates", []) or []:
            ids = [memories[i]["id"] for i in d.get("ids", []) if isinstance(i, int) and 0 <= i < len(memories)]
            if len(ids) >= 2:
                duplicates.append({"ids": ids, "merged_text": d.get("merged_text", "")[:2000]})
        contradictions = []
        for c in parsed.get("contradictions", []) or []:
            ids = [memories[i]["id"] for i in c.get("ids", []) if isinstance(i, int) and 0 <= i < len(memories)]
            if ids:
                contradictions.append({"ids": ids, "note": str(c.get("note", ""))[:300]})

        if not body.dry_run:
            for d in duplicates:
                merged = d.get("merged_text", "")
                if merged:
                    metadata = build_metadata(
                        scope=SCOPE_WORKSPACE, workspace_id=workspace_id,
                        session_id=body.session_id or "consolidate",
                        memory_type="fact", source="consolidate",
                    )
                    try:
                        self.engine.add(namespace, merged, metadata=metadata, infer=False)
                    except Exception as exc:
                        log.warning("consolidate merged write failed: %s", exc)
                        continue
                for mid in d["ids"]:
                    self.engine.delete(mid)
        return {"ok": True, "dry_run": body.dry_run,
                "duplicates": duplicates, "contradictions": contradictions}

    # ------------------------------------------------------------------
    # export / import
    # ------------------------------------------------------------------
    def export(self, body: ExportRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id)
        parts: list[str] = []
        if body.scope in ("workspace", "both"):
            memories = self.engine.get_all(
                security.workspace_namespace(workspace_id), top_k=500
            )
            parts.append(_export_block("workspace", workspace_id, memories))
        if body.scope in ("global", "both"):
            memories = self.engine.get_all(security.global_namespace(), top_k=500)
            parts.append(_export_block("global", "", memories))
        if body.format == "json":
            return {"ok": True, "format": "json", "data": _iter_export(body)}
        return {"ok": True, "format": "markdown", "content": "\n\n".join(p for p in parts if p)}

    def import_memories(self, body: ImportRequest) -> Dict[str, Any]:
        workspace_id = self._workspace_for(body.session_id)
        if body.scope not in (SCOPE_GLOBAL, SCOPE_WORKSPACE):
            raise ValueError("import scope must be global or workspace")
        namespace = namespace_for(workspace_id, body.scope, None)
        entries = _parse_import(body.content)
        added = 0
        for text in entries:
            metadata = build_metadata(
                scope=body.scope,
                workspace_id=workspace_id if body.scope == SCOPE_WORKSPACE else "",
                session_id=body.session_id or "import",
                memory_type="fact", source="import",
            )
            try:
                self.engine.add(namespace, text, metadata=metadata, infer=False)
                added += 1
            except Exception as exc:
                log.warning("import failed for one entry: %s", exc)
        return {"ok": True, "imported": added}

    # ------------------------------------------------------------------
    # retry worker
    # ------------------------------------------------------------------
    def _retry_loop(self) -> None:
        while not self._stop.is_set():
            try:
                tasks = self.store.claim_due_retries(limit=5)
                for task in tasks:
                    try:
                        if task["kind"] == "capture_facts":
                            payload = json.loads(task["payload"])
                            result = self.capture_engine.capture_facts(
                                payload["session_id"], payload["workspace_id"],
                                payload["transcript_path"], payload.get("source", "retry"),
                            )
                            if result.get("captured"):
                                self.store.drop_retry(task["task_id"])
                            else:
                                self.store.bump_retry(task["task_id"], result.get("reason", "failed"))
                        else:
                            self.store.drop_retry(task["task_id"])
                    except Exception as exc:
                        self.store.bump_retry(task["task_id"], str(exc))
            except Exception as exc:
                log.warning("retry worker error: %s", exc)
            self._stop.wait(RETRY_WORKER_INTERVAL)

    def shutdown(self) -> None:
        self._stop.set()
        self.engine.close()
        self.store.close()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
class DaemonHandler(BaseHTTPRequestHandler):
    server_version = "mem0-rvaim/0.3"

    app: DaemonApp = None  # injected by the server factory
    token: str = ""

    # ------------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(security.scrub_secrets(fmt % args))

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc

    def _check_auth(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        return security.constant_time_equal(header, expected)

    # ------------------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        if not self._check_auth():
            self._send(401, {"error": "unauthorized"})
            return
        path = self.path.split("?")[0]
        try:
            if method == "GET" and path == "/health":
                self._send(200, self.app.health())
            elif method == "POST" and path == "/v1/session/register":
                self._send(200, self.app.register_session(SessionRegisterRequest(**self._read_body())))
            elif method == "POST" and path == "/v1/recall":
                self._send(200, self.app.recall(RecallRequest(**self._read_body())))
            elif method == "POST" and path == "/v1/capture":
                self._send(200, self.app.capture(CaptureRequest(**self._read_body())))
            elif method == "POST" and path == "/v1/memories/add":
                self._send(200, self.app.add_memory(AddMemoryRequest(**self._read_body())))
            elif method == "POST" and path == "/v1/memories/search":
                self._send(200, self.app.search_memories(SearchMemoriesRequest(**self._read_body())))
            elif method == "POST" and path == "/v1/memories/get":
                self._send(200, self.app.get_memories(GetMemoriesRequest(**self._read_body())))
            elif method == "GET" and path.startswith("/v1/memories/"):
                memory_id = path[len("/v1/memories/"):]
                self._send(200, self.app.get_memory(memory_id))
            elif method == "PATCH" and path == "/v1/memories/":
                self._send(200, self.app.update_memory(UpdateMemoryRequest(**self._read_body())))
            elif method == "PATCH" and path.startswith("/v1/memories/"):
                memory_id = path[len("/v1/memories/"):]
                body = self._read_body() or {}
                body["memory_id"] = memory_id
                self._send(200, self.app.update_memory(UpdateMemoryRequest(**body)))
            elif method == "DELETE" and path.startswith("/v1/memories/"):
                memory_id = path[len("/v1/memories/"):]
                self._send(200, self.app.delete_memory(DeleteMemoryRequest(memory_id=memory_id)))
            elif method == "POST" and path == "/v1/memories/delete_all":
                self._send(200, self.app.delete_all_memories(DeleteAllMemoriesRequest(**self._read_body())))
            elif method == "POST" and path == "/v1/memories/consolidate":
                self._send(200, self.app.consolidate(ConsolidateRequest(**self._read_body())))
            elif method == "GET" and path == "/v1/workspaces":
                self._send(200, self.app.list_workspaces())
            elif method == "POST" and path == "/v1/entities/list":
                self._send(200, self.app.list_entities(ListEntitiesRequest(**self._read_body())))
            elif method == "POST" and path == "/v1/entities/delete":
                self._send(200, self.app.delete_entities(DeleteEntitiesRequest(**self._read_body())))
            elif method == "POST" and path == "/v1/export":
                self._send(200, self.app.export(ExportRequest(**self._read_body())))
            elif method == "POST" and path == "/v1/import":
                self._send(200, self.app.import_memories(ImportRequest(**self._read_body())))
            elif method == "GET" and path == "/v1/stats":
                self._send(200, self.app.stats(None))
            elif method == "GET" and path.startswith("/v1/events/"):
                event_id = path[len("/v1/events/"):]
                self._send(200, self.app.event_status(event_id))
            elif method == "POST" and path == "/v1/admin/reload":
                self._send(200, self.app.reload_config())
            else:
                self._send(404, {"error": f"not found: {method} {path}"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except Exception as exc:  # never crash the daemon on a bad request
            log.warning("request failed: %s", exc)
            self._send(500, {"error": f"internal error: {type(exc).__name__}"})

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")


def create_server(data_dir: Path, port: int = 0, cfg: Optional[Dict[str, Any]] = None) -> ThreadingHTTPServer:
    app = DaemonApp(data_dir, cfg)
    token = security.generate_token()

    server = ThreadingHTTPServer(("127.0.0.1", port), DaemonHandler)
    server.app = app  # type: ignore[attr-defined]
    server.daemon_token = token  # type: ignore[attr-defined]
    DaemonHandler.app = app
    DaemonHandler.token = token
    return server


def run_server(data_dir: Path, port: int = 0, cfg: Optional[Dict[str, Any]] = None) -> None:
    server = create_server(data_dir, port, cfg)
    app: DaemonApp = server.app  # type: ignore[attr-defined]
    token: str = server.daemon_token  # type: ignore[attr-defined]

    # Another daemon already owns the Qdrant folder (it beat us to the
    # lock during a spawn race).  Serving alongside it would degrade both
    # and overwrite the runtime files — exit quietly instead.
    err = getattr(app.engine, "init_error", None)
    if err is not None and "already accessed by another instance" in str(err):
        log.warning("another daemon owns %s; exiting (pid=%d)", data_dir, os.getpid())
        app.shutdown()
        return

    # persist runtime files under the *config root* runtime/ dir so
    # bootstrap (which reads root/runtime/daemon.port) finds them
    runtime = app_config.root_dir() / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    security.write_token(token)
    (runtime / "daemon.pid").write_text(str(os.getpid()), encoding="utf-8")
    (runtime / "daemon.port").write_text(str(server.server_address[1]), encoding="utf-8")

    def _signal_shutdown(*_args: Any) -> None:
        app.shutdown()
        threading.Thread(target=server.shutdown, daemon=True).start()

    import signal as _signal

    for sig in ("SIGINT", "SIGTERM"):
        handler = getattr(_signal, sig, None)
        if handler is None:
            continue
        try:
            _signal.signal(handler, _signal_shutdown)
        except (ValueError, OSError):
            pass  # non-main thread / Windows

    log.info("daemon listening on 127.0.0.1:%d pid=%d", server.server_address[1], os.getpid())
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        app.shutdown()
        for name in ("daemon.pid", "daemon.port"):
            try:
                (runtime / name).unlink(missing_ok=True)
            except OSError:
                pass
        server.server_close()


def _export_block(scope: str, workspace_id: str, memories: list[Dict[str, Any]]) -> str:
    lines = [f"# mem0-rvaim export ({scope})", ""]
    for m in memories:
        mid = str(m.get("id", "?"))
        text = str(m.get("memory", "")).replace("\n", " ")[:500]
        lines.append(f"- [{mid}] {text}")
    return "\n".join(lines)


def _iter_export(body: ExportRequest) -> list[Dict[str, Any]]:
    """Collect export entries for the JSON format (uses app state)."""
    entries: list[Dict[str, Any]] = []
    app = DaemonHandler.app
    if app is None:
        return entries
    workspace_id = app._workspace_for(body.session_id)
    if body.scope in ("workspace", "both"):
        for m in app.engine.get_all(security.workspace_namespace(workspace_id), top_k=500):
            entries.append({"scope": "workspace", "workspace_id": workspace_id, "memory": m})
    if body.scope in ("global", "both"):
        for m in app.engine.get_all(security.global_namespace(), top_k=500):
            entries.append({"scope": "global", "workspace_id": "", "memory": m})
    return entries


def _parse_import(content: str) -> list[str]:
    """Parse a markdown export file (or raw text lines) into memory texts."""
    entries: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if line.startswith("- ["):
            # "[id] text" format
            entries.append(line.split("]", 1)[1].strip())
        elif line.startswith("- "):
            entries.append(line[2:].strip())
        elif len(line) > 5:
            entries.append(line)
    return entries


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    logging.basicConfig(
        level=logging.DEBUG if app_config.load_config().get("debug") else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    port = 0
    data_dir = app_config.ensure_root() / "data"
    for i, arg in enumerate(argv):
        if arg == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
        elif arg == "--data-dir" and i + 1 < len(argv):
            data_dir = Path(argv[i + 1])
    run_server(data_dir, port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
