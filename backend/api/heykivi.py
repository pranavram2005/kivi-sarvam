"""Hey Kivi (Screen 2) and the query log the Inspector (Screen 4) reads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.config import get_settings
from backend.memory import store
from backend.memory.heykivi import ask
from backend.memory.text import normalise
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


# What each kind of memory makes it sensible to ask next. Keyed on the memory
# that would answer it, so a suggestion is never offered unless something in
# the store can actually answer it.
_FOLLOW_UP_TEMPLATES: dict[str, str] = {
    "event": "When is my {noun} with {subject}?",
    "task": "What do I owe {subject}?",
    "fact": "What do I know about {subject}?",
    "episode": "What was I discussing with {subject}?",
    "preference": "How do I prefer {attribute}?",
}


@router.get("/follow-ups", response_model=list[str])
def follow_ups(query_id: int = Query(...), limit: int = Query(default=3, ge=1, le=6)) -> list[str]:
    """What to ask next, grounded in the answer that was just given.

    The starter questions on an empty screen come from the whole store. Once a
    question has been answered that is the wrong source: the useful next
    question is about what this answer touched, not about the corpus at large.

    So this walks the memories the answer actually cited, takes the people and
    projects in them, and offers a question for each - built from a *different*
    memory about that subject, so the suggestion leads somewhere new rather than
    re-asking what was just answered. A suggestion is only offered when a memory
    exists that would answer it; nothing here proposes a question Kivi would
    have to refuse.
    """
    settings = get_settings()
    user_id = settings.default_user_id

    log = store.get_query_log(query_id)
    if log is None:
        return []

    asked = normalise(log.get("question") or "")
    seeds: list[str] = []
    for memory in store.get_memories(log.get("used_memory_ids") or []):
        subject = (memory.get("subject") or "").strip()
        if subject and subject.lower() != "user" and subject not in seeds:
            seeds.append(subject)
        for entity in memory.get("entities") or []:
            entity = entity.strip()
            if entity and entity not in seeds:
                seeds.append(entity)

    out: list[str] = []
    for subject in seeds:
        # Another memory about the same subject - not the one just used, so the
        # suggestion opens something rather than repeating it.
        others = [
            m
            for m in store.memories_about(user_id=user_id, subject=subject)
            if m["id"] not in (log.get("used_memory_ids") or [])
        ]
        for memory in others:
            template = _FOLLOW_UP_TEMPLATES.get(memory.get("type") or "")
            if not template:
                continue
            question = template.format(
                subject=subject,
                noun=_noun_for(memory),
                attribute=(memory.get("attribute") or "things").replace("_", " "),
            )
            # Never suggest what was just asked, and never the same twice.
            if normalise(question) == asked or question in out:
                continue
            out.append(question)
            break
        if len(out) >= limit:
            break

    # A superseded memory in the chain means there is a real "before" to ask about.
    if len(out) < limit:
        for memory in store.get_memories(log.get("used_memory_ids") or []):
            if store.was_corrected(memory["id"]):
                question = f"What did I say about {memory.get('subject') or 'this'} before?"
                if question not in out:
                    out.append(question)
                break

    return out[:limit]


def _noun_for(memory: dict[str, Any]) -> str:
    """The word to call an event in a question - taken from the memory itself."""
    for word in ("standup", "review", "sync", "demo", "walkthrough", "sign-off", "call"):
        if word in (memory.get("content") or "").lower():
            return word
    return "meeting"


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
