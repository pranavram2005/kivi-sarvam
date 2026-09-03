"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MemoryType = Literal["fact", "preference", "episode", "task", "event"]
MemoryStatus = Literal["ACTIVE", "SUPERSEDED", "DELETED", "REJECTED"]


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------
class TranscriptIn(BaseModel):
    """One dictation, in the documented corpus format.

    `formatted_output` is accepted as an alias for `formatted_text` because the
    corpus format in the assignment uses the former and the database uses the
    latter; reviewers should not have to care which.
    """

    id: str | None = Field(default=None, description="Your id for this record; kept for traceability.")
    raw_asr: str = Field(default="", description="Unpolished speech recogniser output.")
    formatted_text: str = Field(default="", description="What Kivi typed.")
    formatted_output: str | None = Field(default=None, description="Alias for formatted_text.")
    timestamp: str = Field(default="", description="ISO-8601 time of the dictation.")
    application: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _default_timestamp(cls, value: str) -> str:
        return value or datetime.now(timezone.utc).isoformat(timespec="seconds")

    def text(self) -> str:
        return self.formatted_text or self.formatted_output or self.raw_asr

    def asr(self) -> str:
        return self.raw_asr or self.text()


class TranscriptOut(BaseModel):
    id: int
    external_id: str | None = None
    raw_asr: str
    formatted_text: str
    application: str | None = None
    timestamp: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    processed_at: str | None = None
    memory_count: int = 0
    extraction_decision: str | None = None


class TranscriptDetail(TranscriptOut):
    memories: list[dict[str, Any]] = Field(default_factory=list)
    extraction: dict[str, Any] | None = None


class TranscriptDay(BaseModel):
    """The dictation feed, grouped the way Screen 1 renders it."""

    label: str
    date: str
    transcripts: list[TranscriptOut]


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------
class MemoryOut(BaseModel):
    id: int
    type: MemoryType | str
    content: str
    subject: str | None = None
    attribute: str | None = None
    value: str | None = None
    entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: float
    status: MemoryStatus | str
    source_transcript_id: int | None = None
    superseded_by_id: int | None = None
    occurred_at: str | None = None
    created_at: str
    updated_at: str


class MemoryDetail(MemoryOut):
    """A memory plus the whole chain that explains it."""

    source_transcript: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)


class MemoryPatch(BaseModel):
    """User corrections from the What Kivi Knows screen."""

    content: str | None = None
    type: MemoryType | None = None
    subject: str | None = None
    attribute: str | None = None
    value: str | None = None
    status: MemoryStatus | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = Field(default=None, description="Why the user made this change.")


class MemoryGroup(BaseModel):
    """Memories arranged as a person would expect to see them."""

    key: str
    label: str
    subtitle: str | None = None
    memories: list[MemoryOut]


class KnowledgeView(BaseModel):
    people: list[MemoryGroup]
    projects: list[MemoryGroup]
    preferences: list[MemoryOut]
    upcoming: list[MemoryOut]
    commitments: list[MemoryOut]
    counts: dict[str, int]


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------
class ProcessRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=5000)
    reprocess_all: bool = False


class ProcessResponse(BaseModel):
    processed: int
    remembered: int
    ignored: int
    memories_created: int
    memories_rejected: int
    memories_superseded: int
    memories_duplicate: int
    conflicts: int
    elapsed_ms: float
    provider: str
    model: str
    results: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Hey Kivi
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=40)


class QueryResponse(BaseModel):
    query_id: int | None = None
    question: str
    answer: str
    abstained: bool
    conflict: bool
    supported: bool
    confidence: float
    reasoning: str
    intent: str
    entities: list[str]
    retrieved_memory_ids: list[int]
    used_memory_ids: list[int]
    sources: list[dict[str, Any]]
    retrieval_detail: list[dict[str, Any]]
    diagnostics: dict[str, Any]


# ---------------------------------------------------------------------------
# Corpus import
# ---------------------------------------------------------------------------
class CorpusImportRequest(BaseModel):
    records: list[TranscriptIn]
    process: bool = Field(default=True, description="Run memory extraction after importing.")
    reset: bool = Field(default=False, description="Clear existing data for this user first.")


class CorpusImportResponse(BaseModel):
    imported: int
    skipped: int
    errors: list[str]
    processed: ProcessResponse | None = None


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class SystemStatus(BaseModel):
    user_id: str
    llm_provider: str
    llm_model: str
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    database: str
    transcripts: int
    transcripts_unprocessed: int
    memories: dict[str, int]
    memory_types: dict[str, int]
    queries: int
    extraction: dict[str, Any]
    offline_mode: bool
