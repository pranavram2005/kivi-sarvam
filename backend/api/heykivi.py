"""Hey Kivi (Screen 2) and the query log the Inspector (Screen 4) reads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.config import get_settings
from backend.memory import store
from backend.memory.heykivi import ask
from backend.models.schemas import QueryRequest, QueryResponse

router = APIRouter(prefix="/api/hey-kivi", tags=["hey-kivi"])


@router.post("/query", response_model=QueryResponse)
def query(payload: QueryRequest) -> QueryResponse:
    """Answer a question from memory - or say honestly that memory has nothing."""
    settings = get_settings()
    result = ask(payload.question, user_id=settings.default_user_id, top_k=payload.top_k)
    return QueryResponse(**result.as_dict())


@router.get("/history")
def history(limit: int = Query(default=50, ge=1, le=500)) -> list[dict[str, Any]]:
    """Past Hey Kivi turns, newest first.

    Shaped like a live answer so the same conversation view can render a
    restored turn without a second code path: the log already holds latency,
    model and cost as flat columns, and they are gathered into `diagnostics`
    here rather than left for the client to reassemble.
    """
    settings = get_settings()
    logs = store.list_query_logs(user_id=settings.default_user_id, limit=limit)
    for log in logs:
        log["abstained"] = bool(log["abstained"])
        log["conflict"] = bool(log["conflict"])
        log["supported"] = bool(log["supported"])
        log["diagnostics"] = {
            "retrieval_latency_ms": log.get("retrieval_latency_ms"),
            "llm_latency_ms": log.get("llm_latency_ms"),
            "total_latency_ms": log.get("total_latency_ms"),
            "input_tokens": log.get("input_tokens"),
            "output_tokens": log.get("output_tokens"),
            "total_tokens": (log.get("input_tokens") or 0) + (log.get("output_tokens") or 0),
            "estimated_cost_usd": log.get("cost_usd"),
            "provider": log.get("provider"),
            "model": log.get("model"),
        }
    return logs


@router.get("/queries/{query_id}")
def query_detail(query_id: int) -> dict[str, Any]:
    """One turn, expanded: the retrieval ranking, what was used, and why."""
    log = store.get_query_log(query_id)
    if log is None:
        raise HTTPException(status_code=404, detail=f"No query #{query_id}.")

    log["abstained"] = bool(log["abstained"])
    log["conflict"] = bool(log["conflict"])
    log["supported"] = bool(log["supported"])

    used_ids = log.get("used_memory_ids") or []
    retrieved_ids = log.get("retrieved_memory_ids") or []
    memories = {m["id"]: m for m in store.get_memories(list({*used_ids, *retrieved_ids}))}

    def expand(memory_id: int) -> dict[str, Any]:
        memory = memories.get(memory_id, {})
        transcript = (
            store.get_transcript(memory["source_transcript_id"])
            if memory.get("source_transcript_id")
            else None
        )
        return {
            "memory_id": memory_id,
            "content": memory.get("content"),
            "type": memory.get("type"),
            "status": memory.get("status"),
            "confidence": memory.get("confidence"),
            "source_transcript_id": memory.get("source_transcript_id"),
            "source_text": (transcript or {}).get("formatted_text"),
            "source_timestamp": (transcript or {}).get("timestamp"),
            "source_application": (transcript or {}).get("application"),
        }

    log["used"] = [expand(i) for i in used_ids]
    log["retrieved"] = [expand(i) for i in retrieved_ids]
    return log


@router.get("/suggestions", response_model=list[str])
def suggestions() -> list[str]:
    """Starter questions grounded in this user's actual memory.

    Generated from the entities Kivi has really learned, so the suggestions on
    an imported reviewer corpus are about that corpus - not a hardcoded demo.
    """
    settings = get_settings()
    user_id = settings.default_user_id

    people: list[str] = []
    projects: list[str] = []
    for record in store.list_memories(user_id=user_id, limit=400):
        subject = (record.get("subject") or "").strip()
        if not subject or subject.lower() == "user":
            continue
        target = projects if subject.lower().startswith(("project", "initiative")) else people
        if subject not in target:
            target.append(subject)

    out: list[str] = []
    if people:
        out.append(f"What do I need to prepare for {people[0]}?")
        out.append(f"When is my meeting with {people[0]}?")
    if len(people) > 1:
        out.append(f"What was I discussing with {people[1]}?")
    if projects:
        out.append(f"What did I say about {projects[0]}?")
    if people:
        out.append(f"Draft a short message to {people[0]} about the meeting.")
        out.append(f"When is {people[0]}'s birthday?")

    return out[:6] or [
        "What are my upcoming meetings?",
        "What have I committed to this week?",
        "How do I prefer my emails written?",
    ]
