"""What Kivi Knows (Screen 3): inspect, correct and forget memories.

The grouped view is deliberately shaped around people, projects, preferences
and commitments rather than around the database. A user should be able to fix
what Kivi believes without ever meeting a row id or an embedding.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.config import get_settings
from backend.llm.embeddings import get_embedder
from backend.memory import store
from backend.memory.heykivi import provenance_chain
from backend.memory.store import ACTIVE, DELETED, REJECTED, SUPERSEDED
from backend.models.schemas import (
    KnowledgeView,
    MemoryDetail,
    MemoryGroup,
    MemoryOut,
    MemoryPatch,
    ProcessRequest,
    ProcessResponse,
)

router = APIRouter(prefix="/api/memories", tags=["memories"])

PROJECT_PREFIXES = ("project", "initiative", "program", "workstream")


def _out(record: dict[str, Any]) -> MemoryOut:
    return MemoryOut(**{k: record.get(k) for k in MemoryOut.model_fields})


@router.get("", response_model=list[MemoryOut])
def list_memories(
    status: list[str] = Query(default=["ACTIVE"]),
    type: list[str] | None = Query(default=None),
    subject: str | None = None,
    search: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> list[MemoryOut]:
    settings = get_settings()
    records = store.list_memories(
        user_id=settings.default_user_id,
        statuses=tuple(status),
        memory_types=tuple(type) if type else None,
        subject=subject,
        search=search,
        limit=limit,
        offset=offset,
    )
    return [_out(r) for r in records]


@router.get("/knowledge", response_model=KnowledgeView)
def knowledge_view() -> KnowledgeView:
    """Everything Kivi currently believes, arranged for a person to read."""
    settings = get_settings()
    user_id = settings.default_user_id
    records = store.list_memories(user_id=user_id, statuses=(ACTIVE,), limit=2000)

    people: dict[str, list[dict[str, Any]]] = defaultdict(list)
    projects: dict[str, list[dict[str, Any]]] = defaultdict(list)
    preferences: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    commitments: list[dict[str, Any]] = []

    for record in records:
        if record["type"] == "preference":
            preferences.append(record)
            continue
        if record["type"] == "event":
            upcoming.append(record)
        if record["type"] == "task":
            commitments.append(record)

        subject = (record.get("subject") or "").strip()
        if not subject or subject.lower() == "user":
            continue
        if subject.lower().startswith(PROJECT_PREFIXES):
            projects[subject].append(record)
        else:
            people[subject].append(record)

        # A memory about a person that also names a project belongs under both.
        for entity in record.get("entities") or []:
            if entity.lower().startswith(PROJECT_PREFIXES) and entity != subject:
                projects[entity].append(record)

    def build(groups: dict[str, list[dict[str, Any]]]) -> list[MemoryGroup]:
        out: list[MemoryGroup] = []
        for key, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            unique: dict[int, dict[str, Any]] = {m["id"]: m for m in items}
            ordered = sorted(unique.values(), key=lambda m: m["id"], reverse=True)
            out.append(
                MemoryGroup(
                    key=key,
                    label=key,
                    subtitle=_subtitle(ordered),
                    memories=[_out(m) for m in ordered],
                )
            )
        return out

    upcoming.sort(key=lambda m: (m.get("occurred_at") or "9999", m["id"]))

    return KnowledgeView(
        people=build(people),
        projects=build(projects),
        preferences=[_out(m) for m in preferences],
        upcoming=[_out(m) for m in upcoming],
        commitments=[_out(m) for m in commitments],
        counts={
            **store.memory_counts(user_id),
            **{f"type_{k}": v for k, v in store.memory_type_counts(user_id).items()},
        },
    )


def _subtitle(memories: list[dict[str, Any]]) -> str | None:
    """A one-line summary for a group header, in product language."""
    for memory in memories:
        if memory["type"] == "fact" and memory.get("value"):
            attribute = (memory.get("attribute") or "").replace("_", " ")
            return f"{attribute}: {memory['value']}".strip(": ")
    for memory in memories:
        if memory["type"] == "event" and memory.get("value"):
            return f"Next: {memory['value']}"
    projects = [
        e
        for m in memories
        for e in (m.get("entities") or [])
        if e.lower().startswith(PROJECT_PREFIXES)
    ]
    if projects:
        return f"Connected to {projects[0]}"
    return None


@router.get("/{memory_id}", response_model=MemoryDetail)
def get_memory(memory_id: int) -> MemoryDetail:
    """One memory with its full provenance chain."""
    chain = provenance_chain(memory_id)
    if not chain:
        raise HTTPException(status_code=404, detail=f"No memory #{memory_id}.")
    memory = chain["memory"]
    return MemoryDetail(
        **{k: memory.get(k) for k in MemoryOut.model_fields},
        source_transcript=chain.get("transcript"),
        events=chain.get("events") or [],
        relations=chain.get("relations") or [],
    )


@router.patch("/{memory_id}", response_model=MemoryDetail)
def correct_memory(memory_id: int, patch: MemoryPatch) -> MemoryDetail:
    """Apply a user correction.

    User edits are recorded in the audit log with actor='user', so the
    Inspector can distinguish what Kivi learned from what a person fixed.
    """
    existing = store.get_memory(memory_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No memory #{memory_id}.")

    fields = {k: v for k, v in patch.model_dump(exclude_none=True).items() if k != "reason"}
    if not fields:
        raise HTTPException(status_code=422, detail="Nothing to change.")

    embedding = None
    embedder = get_embedder()
    if "content" in fields:
        # The vector has to follow the text, or retrieval keeps finding the
        # sentence the user just corrected.
        parts = [fields["content"], existing.get("subject") or ""]
        parts += existing.get("entities") or []
        embedding = embedder.embed(" ".join(p for p in parts if p))

    updated = store.update_memory(
        memory_id,
        fields=fields,
        embedding=embedding,
        embedding_model=embedder.model if embedding else None,
    )
    store.log_event(
        memory_id=memory_id,
        transcript_id=existing.get("source_transcript_id"),
        event="EDITED",
        reason=patch.reason or "corrected by the user",
        detail={"before": {k: existing.get(k) for k in fields}, "after": fields},
        actor="user",
    )
    return get_memory(memory_id)


@router.delete("/{memory_id}", response_model=MemoryDetail)
def forget_memory(memory_id: int, reason: str | None = None) -> MemoryDetail:
    """Forget a memory.

    The row is kept with status DELETED rather than removed: forgetting should
    be reversible, and a memory that vanishes without trace is impossible to
    audit. It is excluded from every retrieval path.
    """
    existing = store.get_memory(memory_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No memory #{memory_id}.")

    store.set_status(memory_id, DELETED)
    store.log_event(
        memory_id=memory_id,
        transcript_id=existing.get("source_transcript_id"),
        event="FORGOTTEN",
        reason=reason or "the user asked Kivi to forget this",
        actor="user",
    )
    return get_memory(memory_id)


@router.post("/{memory_id}/restore", response_model=MemoryDetail)
def restore_memory(memory_id: int) -> MemoryDetail:
    """Bring back a forgotten or rejected memory."""
    existing = store.get_memory(memory_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No memory #{memory_id}.")
    if existing["status"] not in (DELETED, REJECTED, SUPERSEDED):
        raise HTTPException(status_code=409, detail="That memory is already active.")

    store.set_status(memory_id, ACTIVE)
    store.log_event(
        memory_id=memory_id,
        transcript_id=existing.get("source_transcript_id"),
        event="REINSTATED",
        reason=f"restored by the user from {existing['status']}",
        actor="user",
    )
    return get_memory(memory_id)


# ---------------------------------------------------------------------------
process_router = APIRouter(prefix="/api/memory", tags=["memory"])


@process_router.post("/process", response_model=ProcessResponse)
def process_transcripts(payload: ProcessRequest) -> ProcessResponse:
    """Run memory extraction over transcripts that have not been processed."""
    import time

    from backend.llm.engine import get_engine
    from backend.memory import extractor

    settings = get_settings()
    engine = get_engine()

    if payload.reprocess_all:
        connection = store.get_connection()
        connection.execute(
            "UPDATE transcripts SET processed_at = NULL WHERE user_id = ?",
            (settings.default_user_id,),
        )
        connection.commit()

    started = time.perf_counter()
    results = extractor.process_pending(
        user_id=settings.default_user_id, limit=payload.limit, engine=engine
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    return ProcessResponse(
        processed=len(results),
        remembered=sum(1 for r in results if r.decision == "REMEMBER"),
        ignored=sum(1 for r in results if r.decision == "IGNORE"),
        memories_created=sum(r.created for r in results),
        memories_rejected=sum(r.rejected for r in results),
        memories_superseded=sum(r.superseded for r in results),
        memories_duplicate=sum(r.duplicates for r in results),
        conflicts=sum(r.conflicts for r in results),
        elapsed_ms=round(elapsed_ms, 2),
        provider=engine.name,
        model=engine.model,
        results=[r.as_dict() for r in results[:200]],
    )
