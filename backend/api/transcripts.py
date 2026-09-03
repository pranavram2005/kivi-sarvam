"""Transcript ingestion and the dictation feed (Screen 1)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.config import get_settings
from backend.memory import extractor, store
from backend.models.schemas import (
    TranscriptDay,
    TranscriptDetail,
    TranscriptIn,
    TranscriptOut,
)

router = APIRouter(prefix="/api/transcripts", tags=["transcripts"])


def _memory_counts_by_transcript(transcript_ids: list[int]) -> dict[int, int]:
    if not transcript_ids:
        return {}
    connection = store.get_connection()
    placeholders = ",".join("?" * len(transcript_ids))
    rows = connection.execute(
        f"""
        SELECT source_transcript_id AS tid, COUNT(*) AS n
        FROM memories
        WHERE source_transcript_id IN ({placeholders}) AND status IN ('ACTIVE','SUPERSEDED')
        GROUP BY source_transcript_id
        """,
        transcript_ids,
    ).fetchall()
    return {int(r["tid"]): int(r["n"]) for r in rows}


def _decisions_by_transcript(transcript_ids: list[int]) -> dict[int, str]:
    if not transcript_ids:
        return {}
    connection = store.get_connection()
    placeholders = ",".join("?" * len(transcript_ids))
    rows = connection.execute(
        f"""
        SELECT transcript_id, decision FROM extraction_runs
        WHERE transcript_id IN ({placeholders})
        ORDER BY id ASC
        """,
        transcript_ids,
    ).fetchall()
    return {int(r["transcript_id"]): r["decision"] for r in rows}


def _to_out(record: dict[str, Any], counts: dict[int, int], decisions: dict[int, str]) -> TranscriptOut:
    return TranscriptOut(
        id=record["id"],
        external_id=record.get("external_id"),
        raw_asr=record["raw_asr"],
        formatted_text=record["formatted_text"],
        application=record.get("application"),
        timestamp=record["timestamp"],
        metadata=record.get("metadata") or {},
        processed_at=record.get("processed_at"),
        memory_count=counts.get(record["id"], 0),
        extraction_decision=decisions.get(record["id"]),
    )


@router.post("", response_model=TranscriptDetail, status_code=201)
def create_transcript(payload: TranscriptIn, process: bool = Query(default=True)) -> TranscriptDetail:
    """Ingest one dictation. The original text is always stored, unchanged.

    By default the new transcript is put through memory extraction immediately,
    which is what makes the History screen's "dictate something" flow feel live.
    """
    settings = get_settings()
    text = payload.text()
    if not text.strip():
        raise HTTPException(status_code=422, detail="A transcript needs some text.")

    transcript_id = store.insert_transcript(
        user_id=settings.default_user_id,
        raw_asr=payload.asr(),
        formatted_text=text,
        timestamp=payload.timestamp,
        application=payload.application,
        metadata=payload.metadata,
        external_id=payload.id,
    )

    if process:
        transcript = store.get_transcript(transcript_id)
        if transcript:
            extractor.process_transcript(transcript, user_id=settings.default_user_id)

    return get_transcript(transcript_id)


@router.get("", response_model=list[TranscriptOut])
def list_transcripts(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    application: str | None = None,
) -> list[TranscriptOut]:
    settings = get_settings()
    records = store.list_transcripts(
        user_id=settings.default_user_id,
        limit=limit,
        offset=offset,
        search=search,
        application=application,
    )
    ids = [r["id"] for r in records]
    counts = _memory_counts_by_transcript(ids)
    decisions = _decisions_by_transcript(ids)
    return [_to_out(r, counts, decisions) for r in records]


@router.get("/feed", response_model=list[TranscriptDay])
def transcript_feed(
    limit: int = Query(default=120, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    application: str | None = None,
) -> list[TranscriptDay]:
    """The feed grouped by day, with Today / Yesterday labels."""
    settings = get_settings()
    records = store.list_transcripts(
        user_id=settings.default_user_id,
        limit=limit,
        offset=offset,
        search=search,
        application=application,
    )
    ids = [r["id"] for r in records]
    counts = _memory_counts_by_transcript(ids)
    decisions = _decisions_by_transcript(ids)

    # "Today" is relative to the newest dictation, not to the wall clock: the
    # corpus is historical, and a feed that says every entry is from 2026 is
    # less readable than one anchored to the data itself.
    newest: date | None = None
    for record in records:
        parsed = _parse_date(record["timestamp"])
        if parsed and (newest is None or parsed > newest):
            newest = parsed

    grouped: dict[str, list[TranscriptOut]] = {}
    order: list[str] = []
    for record in records:
        parsed = _parse_date(record["timestamp"])
        key = parsed.isoformat() if parsed else "unknown"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(_to_out(record, counts, decisions))

    days: list[TranscriptDay] = []
    for key in order:
        days.append(
            TranscriptDay(label=_day_label(key, newest), date=key, transcripts=grouped[key])
        )
    return days


@router.get("/applications", response_model=list[str])
def list_applications() -> list[str]:
    return store.applications(get_settings().default_user_id)


@router.get("/{transcript_id}", response_model=TranscriptDetail)
def get_transcript(transcript_id: int) -> TranscriptDetail:
    """One dictation with everything Kivi learned from it."""
    record = store.get_transcript(transcript_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No transcript #{transcript_id}.")

    connection = store.get_connection()
    rows = connection.execute(
        "SELECT * FROM memories WHERE source_transcript_id = ? ORDER BY id", (transcript_id,)
    ).fetchall()
    memories = store.rows_to_dicts(rows)

    counts = {transcript_id: len([m for m in memories if m["status"] != "REJECTED"])}
    decisions = _decisions_by_transcript([transcript_id])
    base = _to_out(record, counts, decisions)

    return TranscriptDetail(
        **base.model_dump(),
        memories=memories,
        extraction=store.extraction_run_for(transcript_id),
    )


# ---------------------------------------------------------------------------
def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def _day_label(key: str, newest: date | None) -> str:
    if key == "unknown":
        return "Undated"
    try:
        day = date.fromisoformat(key)
    except ValueError:
        return key
    if newest:
        if day == newest:
            return "Today"
        if day == newest - timedelta(days=1):
            return "Yesterday"
        if (newest - day).days < 7:
            return day.strftime("%A")
    # %-d is not portable to Windows, so the day number is formatted by hand.
    return f"{day.strftime('%B')} {day.day}, {day.year}"


@router.delete("/{transcript_id}")
def delete_transcript(transcript_id: int, reason: str | None = None) -> dict:
    """Delete a dictation and forget what it taught Kivi.

    Deleting is reversible and leaves an audit trail. The transcript row and
    its memories are kept - the dictation is hidden from History and from
    retrieval, and any memory it produced is marked DELETED, which is the same
    treatment `DELETE /api/memories/{id}` gives a single memory.

    Removing the row instead would cascade through `source_transcript_id` and
    take the memories and their events with it, leaving answers already in the
    query log citing rows that no longer exist. An answer whose provenance
    cannot be reconstructed is worse than one that was never given.
    """
    record = store.get_transcript(transcript_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No transcript #{transcript_id}.")
    if store.is_transcript_deleted(transcript_id):
        raise HTTPException(status_code=409, detail="That dictation is already deleted.")

    forgotten = store.delete_transcript(transcript_id, reason=reason)
    for memory_id in forgotten:
        store.log_event(
            memory_id=memory_id,
            transcript_id=transcript_id,
            event="FORGOTTEN",
            reason=reason or "the dictation it came from was deleted",
            actor="user",
        )
    return {
        "transcript_id": transcript_id,
        "deleted": True,
        "memories_forgotten": forgotten,
    }


@router.post("/{transcript_id}/restore")
def restore_transcript(transcript_id: int) -> dict:
    """Bring back a deleted dictation and the memories it produced."""
    record = store.get_transcript(transcript_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No transcript #{transcript_id}.")
    if not store.is_transcript_deleted(transcript_id):
        raise HTTPException(status_code=409, detail="That dictation is not deleted.")

    restored = store.restore_transcript(transcript_id)
    for memory_id in restored:
        store.log_event(
            memory_id=memory_id,
            transcript_id=transcript_id,
            event="RESTORED",
            reason="the dictation it came from was restored",
            actor="user",
        )
    return {
        "transcript_id": transcript_id,
        "deleted": False,
        "memories_restored": restored,
    }
