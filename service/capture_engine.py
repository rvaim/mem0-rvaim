"""Capture pipelines: facts (infer) and session summaries.

Pipeline A (facts):
    new transcript messages -> Memory LLM scope classification
    -> mem0 add(infer=True) into global/workspace namespace (discard dropped)

Pipeline B (summary):
    Stop/PreCompact -> Summary LLM produces a structured summary
    -> mem0 add(infer=False) with memory_type=session_summary

Both pipelines are idempotent: the transcript cursor advances and event
hashes are recorded only after a successful capture, so repeated Stop /
PreCompact events cannot duplicate memories.  The summary pipeline never
uses the host agent — the daemon's own Summary LLM writes it.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from . import config as app_config
from . import security
from . import transcript_reader
from .memory_engine import MemoryEngine
from .provider_factory import chat_completion, extract_json_object
from .scope_router import (
    SCOPE_DISCARD,
    SCOPE_GLOBAL,
    SCOPE_WORKSPACE,
    build_metadata,
    classify_scope,
    namespace_for,
)
from .state_store import StateStore

log = logging.getLogger("mem0-rvaim.capture")

_SUMMARY_SYSTEM = (
    "You summarize a coding session transcript for a persistent memory system. "
    "Respond with a single JSON object only with these keys:\n"
    '{"goal": string, "key_decisions": [string], "completed_work": [string], '
    '"files_modified": [string], "known_issues": [string], "unfinished": [string], '
    '"constraints": [string]}\n'
    "Be concise. Use the original language of the conversation. "
    "Never mention that you are an AI or that this is a summary request."
)


class CaptureEngine:
    def __init__(self, store: StateStore, engine: MemoryEngine, cfg: Optional[Dict[str, Any]] = None):
        self.store = store
        self.engine = engine
        self.cfg = cfg or app_config.load_config()

    # ------------------------------------------------------------------
    # fact capture
    # ------------------------------------------------------------------
    def capture_facts(
        self,
        session_id: str,
        workspace_id: str,
        transcript_path: str,
        source: str = "stop",
    ) -> Dict[str, Any]:
        """Capture new transcript messages as facts. Returns a result dict."""
        capture_cfg = self.cfg.get("capture") or {}
        min_messages = int(capture_cfg.get("min_messages", 2))
        min_chars = int(capture_cfg.get("min_chars", 100))

        messages, new_offset = transcript_reader.read_incremental(
            self.store, session_id, transcript_path
        )
        if not messages:
            return {"captured": False, "reason": "no-new-messages", "facts": 0}

        # drop already-processed events (duplicate Stop/PreCompact)
        fresh: List[Dict[str, str]] = []
        for msg in messages:
            h = transcript_reader.event_hash(msg["type"], msg["role"], msg["content"])
            if not self.store.is_event_processed(session_id, h):
                fresh.append(msg)
        if not fresh:
            self._advance(session_id, transcript_path, new_offset)
            return {"captured": False, "reason": "all-events-processed", "facts": 0}

        if len(fresh) < min_messages:
            self._advance(session_id, transcript_path, new_offset)
            return {"captured": False, "reason": "too-few-messages", "facts": 0}

        total_chars = sum(len(m["content"]) for m in fresh)
        if total_chars < min_chars:
            self._advance(session_id, transcript_path, new_offset)
            return {"captured": False, "reason": "too-short", "facts": 0}

        conversation = transcript_reader.build_conversation(fresh)
        text_for_classify = "\n".join(m["content"] for m in fresh[-4:])[:6000]

        scope, reason = classify_scope(text_for_classify, self.cfg)
        if scope == SCOPE_DISCARD:
            log.info("capture: LLM marked fragment as discard (%s)", reason)
            self._advance(session_id, transcript_path, new_offset)
            self._mark_all(session_id, fresh)
            return {"captured": False, "reason": "discarded-by-llm", "facts": 0}

        namespace = namespace_for(workspace_id, scope, None)
        metadata = build_metadata(
            scope=scope,
            workspace_id=workspace_id if scope == SCOPE_WORKSPACE else "",
            session_id=session_id,
            memory_type="fact",
            source=f"capture:{source}",
        )
        try:
            results = self.engine.add_messages(
                namespace, conversation, metadata=metadata, infer=True,
                run_id=f"{session_id}:facts",
            )
        except Exception as exc:
            log.warning("fact capture failed for %s: %s", session_id, exc)
            self.store.enqueue_retry(
                f"capture:{session_id}:{transcript_path}",
                "capture_facts",
                {"session_id": session_id, "workspace_id": workspace_id,
                 "transcript_path": transcript_path, "source": source},
            )
            return {"captured": False, "reason": f"error:{exc}", "facts": 0}

        self._advance(session_id, transcript_path, new_offset)
        self._mark_all(session_id, fresh)
        log.info("captured %d facts (%s, scope=%s)", len(results), scope, reason)
        return {"captured": True, "scope": scope, "facts": len(results)}

    # ------------------------------------------------------------------
    # session summary
    # ------------------------------------------------------------------
    def capture_summary(
        self,
        session_id: str,
        workspace_id: str,
        transcript_path: str,
        source: str = "stop",
    ) -> Dict[str, Any]:
        """Generate a structured session summary from the transcript tail."""
        lines = _tail_lines(transcript_path, 2000)
        if not lines:
            return {"captured": False, "reason": "no-transcript"}

        messages = []
        for line in lines:
            parsed = transcript_reader.parse_transcript_line(line)
            if parsed and parsed["type"] == "assistant":
                messages.append(parsed)

        capture_cfg = self.cfg.get("capture") or {}
        min_chars = int(capture_cfg.get("summary_min_chars", 300))
        if not messages:
            return {"captured": False, "reason": "no-assistant-messages"}
        last_text = messages[-1]["content"]
        if len(last_text) < min_chars:
            return {"captured": False, "reason": "assistant-text-too-short"}

        summary_llm = dict(self.cfg.get("summary_llm") or self.cfg.get("llm") or {})
        if not summary_llm.get("api_key") and "openai.com" in (summary_llm.get("base_url") or ""):
            return {"captured": False, "reason": "summary-llm-unconfigured"}

        files = _extract_files(lines)
        user_prompt = (
            "Session transcript tail:\n\n"
            f"{last_text[:12000]}\n\n"
            f"Files touched: {', '.join(files[:10])}"
        )
        try:
            reply = chat_completion(
                summary_llm,
                [
                    {"role": "system", "content": _SUMMARY_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                json_mode=True,
                timeout=60.0,
            )
            parsed = extract_json_object(reply)
        except Exception as exc:
            log.warning("summary generation failed: %s", exc)
            return {"captured": False, "reason": f"summary-error:{exc}"}

        content = _format_summary(parsed)
        if not content:
            return {"captured": False, "reason": "empty-summary"}

        scope = SCOPE_WORKSPACE if workspace_id else SCOPE_GLOBAL
        namespace = namespace_for(workspace_id, scope, None)
        metadata = build_metadata(
            scope=scope,
            workspace_id=workspace_id,
            session_id=session_id,
            memory_type="session_summary",
            source=f"summary:{source}",
        )
        metadata["files_touched"] = files[:20]
        try:
            self.engine.add(
                namespace, content, metadata=metadata, infer=False,
                run_id=f"{session_id}:summary",
            )
        except Exception as exc:
            log.warning("summary store failed: %s", exc)
            return {"captured": False, "reason": f"store-error:{exc}"}
        log.info("session summary stored (%s chars)", len(content))
        return {"captured": True, "chars": len(content)}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _advance(self, session_id: str, transcript_path: str, new_offset: Optional[int]) -> None:
        if new_offset is not None:
            self.store.set_cursor(session_id, transcript_path, new_offset, 0)

    def _mark_all(self, session_id: str, messages: List[Dict[str, str]]) -> None:
        for msg in messages:
            h = transcript_reader.event_hash(msg["type"], msg["role"], msg["content"])
            self.store.mark_event_processed(session_id, h)


def _tail_lines(filepath: str, n: int) -> List[str]:
    try:
        with open(filepath, "rb") as fh:
            fh.seek(0, 2)
            file_size = fh.tell()
            if file_size == 0:
                return []
            chunk = min(file_size, n * 4096)
            fh.seek(max(0, file_size - chunk))
            data = fh.read().decode("utf-8", errors="replace")
            return data.splitlines()[-n:]
    except OSError:
        return []


def _extract_files(lines: List[str]) -> List[str]:
    import re

    seen: List[str] = []
    pattern = re.compile(
        r"[a-zA-Z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx|rs|go|rb|java|sh|yaml|yml|json|toml|md|sql|css|html)"
    )
    for line in lines:
        line = line.strip()
        if '"tool_use"' not in line and '"file_path"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = entry.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = block.get("input", {})
            if not isinstance(inp, dict):
                continue
            fp = inp.get("file_path", "")
            if fp and fp not in seen:
                seen.append(fp)
            command = inp.get("command", "")
            if command:
                for match in pattern.findall(command):
                    if match not in seen:
                        seen.append(match)
    return seen[:20]


def _format_summary(parsed: Dict[str, Any]) -> str:
    def clean(key: str) -> List[str]:
        value = parsed.get(key)
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    goal = clean("goal")
    decisions = clean("key_decisions")
    completed = clean("completed_work")
    files = clean("files_modified")
    issues = clean("known_issues")
    unfinished = clean("unfinished")
    constraints = clean("constraints")

    if not any([goal, decisions, completed, issues, unfinished, constraints]):
        return ""

    def section(title: str, items: List[str]) -> str:
        if not items:
            return ""
        lines = [f"## {title}"]
        lines.extend(f"- {item}" for item in items)
        return "\n".join(lines)

    parts = []
    if goal:
        parts.append(f"## 目标\n{goal[0]}")
    parts.append(section("关键决定", decisions))
    parts.append(section("已完成工作", completed))
    if files:
        parts.append(section("修改文件", files))
    parts.append(section("已知问题", issues))
    parts.append(section("未完成事项", unfinished))
    parts.append(section("后续约束", constraints))
    return "\n\n".join(parts)
