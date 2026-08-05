"""Pydantic request/response models for the daemon HTTP API.

All request bodies are validated here; unknown fields are rejected so a
misbehaving client cannot smuggle internal fields (e.g. ``user_id``,
``namespace``) into the daemon.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

SCOPE_VALUES = ("global", "workspace")
SEARCH_SCOPE_VALUES = ("both", "global", "workspace")


class SessionRegisterRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    cwd: str = Field(..., min_length=1, max_length=4096)
    workspace_id: Optional[str] = Field(None, max_length=512)
    host: Optional[str] = Field(None, max_length=32)  # claude | codex | unknown
    pid: Optional[int] = None
    client: Optional[str] = Field(None, max_length=64)


class RecallRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    session_id: str = Field(..., min_length=1, max_length=256)
    top_k: Optional[int] = Field(None, ge=1, le=50)
    mode: Optional[str] = None  # direct | rewrite (default from config)


class CaptureRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    transcript_path: Optional[str] = Field(None, max_length=4096)
    cwd: Optional[str] = Field(None, max_length=4096)
    source: Optional[str] = Field(None, max_length=32)  # stop | pre-compact
    capture_summary: Optional[bool] = True


class AddMemoryRequest(BaseModel):
    content: Optional[str] = Field(None, max_length=20000)
    messages: Optional[List[Dict[str, Any]]] = None
    scope: Optional[str] = "auto"  # auto | global | workspace
    infer: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = Field(None, max_length=256)
    idempotency_key: Optional[str] = Field(None, max_length=256)

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in ("auto", "global", "workspace"):
            raise ValueError("scope must be auto, global or workspace")
        return v

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, v):
        if v is not None:
            for m in v:
                if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                    raise ValueError("messages must contain role user|assistant entries")
        return v


class SearchMemoriesRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    session_id: str = Field(..., min_length=1, max_length=256)
    scope: Optional[str] = "both"
    top_k: Optional[int] = Field(None, ge=1, le=100)
    threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    memory_type: Optional[str] = Field(None, max_length=64)

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in SEARCH_SCOPE_VALUES:
            raise ValueError("scope must be both, global or workspace")
        return v


class GetMemoriesRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    scope: Optional[str] = "workspace"
    top_k: Optional[int] = Field(None, ge=1, le=500)
    memory_type: Optional[str] = Field(None, max_length=64)

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in SEARCH_SCOPE_VALUES:
            raise ValueError("scope must be both, global or workspace")
        return v


class UpdateMemoryRequest(BaseModel):
    memory_id: str = Field(..., min_length=1, max_length=128)
    session_id: Optional[str] = Field(None, max_length=256)
    text: Optional[str] = Field(None, max_length=20000)
    metadata_patch: Optional[Dict[str, Any]] = None


class DeleteMemoryRequest(BaseModel):
    memory_id: str = Field(..., min_length=1, max_length=128)
    session_id: Optional[str] = Field(None, max_length=256)


class DeleteAllMemoriesRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    scope: Optional[str] = "workspace"  # global | workspace (never "both")

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in SCOPE_VALUES:
            raise ValueError("delete_all scope must be global or workspace")
        return v


class DeleteEntitiesRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    entity_names: List[str] = Field(..., min_length=1, max_length=100)
    entity_type: Optional[str] = None


class ListEntitiesRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    entity_type: Optional[str] = None
    limit: Optional[int] = Field(None, ge=1, le=1000)


class ConsolidateRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    dry_run: Optional[bool] = True


class ReloadRequest(BaseModel):
    session_id: Optional[str] = None


class ExportRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    scope: Optional[str] = "both"
    format: Optional[str] = "markdown"  # markdown | json


class ImportRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=256)
    content: str = Field(..., max_length=2000000)
    scope: Optional[str] = "workspace"
