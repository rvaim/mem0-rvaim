"""Incremental transcript reading for Claude Code / Codex JSONL transcripts.

The daemon keeps a per-session byte offset cursor in SQLite; each capture
call reads only the new lines since the cursor, normalizes them into
(role, content) messages, and computes a stable event hash so repeated
Stop/PreCompact events never re-capture the same content.  The cursor is
advanced only after a successful capture (see capture_engine).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .state_store import StateStore

log = logging.getLogger("mem0-rvaim.transcript")

# System tags never captured (injected instructions / noise)
_SKIP_PREFIXES = ("<system-reminder>", "<private>", "<claude-mem-context>",
                  "<persisted-output>", "<system_instruction>")


def _text_from_content(content) -> str:
    """Normalize Claude/Codex content blocks into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            block_type = block.get("type")
            if block_type == "text":
                parts.append(block.get("text", ""))
            elif block_type == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, str):
                    parts.append(f"[tool_result] {inner[:500]}")
                elif isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(f"[tool_result] {item.get('text', '')[:500]}")
    return "\n".join(parts).strip()


def _tool_use_summary(content) -> str:
    """Extract tool_use blocks as a compact list for context."""
    if not isinstance(content, list):
        return ""
    out: List[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {})
            if isinstance(inp, dict) and inp.get("file_path"):
                out.append(f"{name}:{inp['file_path']}")
            else:
                out.append(name)
    return ", ".join(out) if out else ""


def event_hash(entry_type: str, role: str, content: str) -> str:
    digest = hashlib.sha256()
    digest.update(entry_type.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(role.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(content.encode("utf-8", errors="replace"))
    return digest.hexdigest()[:32]


def parse_transcript_line(line: str) -> Optional[Dict[str, str]]:
    """Parse one JSONL line into {type, role, content} or None if skipped."""
    line = line.strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None

    if entry.get("isCompactSummary") or entry.get("isSidechain"):
        return None
    entry_type = entry.get("type")
    if entry_type not in ("user", "assistant"):
        return None

    message = entry.get("message", {})
    role = message.get("role") or entry_type
    content_raw = message.get("content")

    if entry_type == "user":
        text = _text_from_content(content_raw)
        # skip system-injected user messages
        for prefix in _SKIP_PREFIXES:
            if text.startswith(prefix):
                return None
        if not text or len(text) < 5:
            return None
        return {"type": "user", "role": "user", "content": text[:8000]}

    # assistant
    text = _text_from_content(content_raw)
    tools = _tool_use_summary(content_raw)
    if not text and not tools:
        return None
    if not text:
        text = f"[tool calls: {tools}]"
    elif tools:
        text = f"{text}\n[tool calls: {tools}]"
    return {"type": "assistant", "role": "assistant", "content": text[:8000]}


def read_incremental(
    store: StateStore,
    session_id: str,
    transcript_path: str,
) -> Tuple[List[Dict[str, str]], Optional[int]]:
    """Return (new_messages, new_offset_bytes) since the stored cursor.

    Handles transcript rotation: if the file was rewritten (offset beyond
    EOF) the cursor resets and only the last 200 lines are considered.
    """
    path = Path(transcript_path)
    if not path.is_file():
        return [], None
    try:
        file_size = path.stat().st_size
    except OSError:
        return [], None

    cursor = store.get_cursor(session_id)
    offset = 0
    if cursor and cursor["transcript_path"] == transcript_path:
        offset = cursor["offset_bytes"] or 0

    if offset >= file_size:
        return [], offset  # nothing new

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            if offset > file_size:  # file rotated/truncated
                fh.seek(0)
                offset = 0
            else:
                fh.seek(offset)
            new_text = fh.read()
            new_offset = fh.tell()
    except OSError:
        return [], offset

    lines = new_text.splitlines()
    if offset == 0 and len(lines) > 200:
        # first read of an existing long transcript: process the tail only
        lines = lines[-200:]
        new_offset = None  # caller keeps cursor at full size

    messages: List[Dict[str, str]] = []
    for line in lines:
        parsed = parse_transcript_line(line)
        if parsed:
            messages.append(parsed)
    return messages, new_offset


def build_conversation(messages: List[Dict[str, str]], max_messages: int = 20) -> List[Dict[str, str]]:
    """Convert normalized messages into mem0-friendly role/content list."""
    return [{"role": m["role"], "content": m["content"]} for m in messages[-max_messages:]]
