"""Transcript ingestion and the dictation feed (Screen 1)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.config import get_settings
from backend.database.db import row_to_dict, unpack_vector
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


@router.get("/example")
def worked_example() -> dict[str, Any]:
    """One real dictation, followed all the way through to a stored memory.

    Everything else on the How it works screen describes the pipeline in the
    abstract. This is one actual record from whatever corpus is loaded - not a
    fixture, not a screenshot - walked end to end: the words as the recogniser
    heard them, the words after formatting, what was extracted, whether it was
    trusted, what it replaced, and the vector it is now findable by.

    The example is chosen rather than pinned, because a reviewer runs this on
    their own five hundred dictations and a hardcoded id would point at nothing.
    Candidates are scored by how much of the system one record can demonstrate -
    see `_example_score`.
    """
    settings = get_settings()
    connection = store.get_connection()
    user = settings.default_user_id

    candidates = connection.execute(
        """SELECT t.id, t.raw_asr, t.formatted_text,
                  COUNT(m.id) AS memories,
                  SUM(CASE WHEN old.id IS NOT NULL THEN 1 ELSE 0 END) AS corrections
           FROM transcripts t
           JOIN memories m ON m.source_transcript_id = t.id
           LEFT JOIN memories old ON old.superseded_by_id = m.id
           WHERE m.user_id = ?
           GROUP BY t.id
           ORDER BY corrections DESC, t.id DESC
           LIMIT 60""",
        (user,),
    ).fetchall()
    if not candidates:
        raise HTTPException(status_code=404, detail="Nothing has been learned yet.")

    best = max(candidates, key=_example_score)
    transcript_id = int(best["id"])
    transcript = store.get_transcript(transcript_id)
    if transcript is None:
        raise HTTPException(status_code=404, detail="The example transcript is missing.")

    rows = connection.execute(
        "SELECT * FROM memories WHERE source_transcript_id = ? ORDER BY id",
        (transcript_id,),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        memory = row_to_dict(row)
        # `row_to_dict` drops the embedding on purpose - raw vectors have no
        # business in an API payload - so the blob is read straight off the row
        # here, and only its shape is reported.
        vector = unpack_vector(row["embedding"])
        replaced = connection.execute(
            "SELECT id, content, created_at FROM memories WHERE superseded_by_id = ?",
            (memory["id"],),
        ).fetchone()
        out.append(
            {
                "id": memory["id"],
                "type": memory.get("type"),
                "subject": memory.get("subject"),
                "attribute": memory.get("attribute"),
                "value": memory.get("value"),
                "content": memory.get("content"),
                "confidence": memory.get("confidence"),
                "status": memory.get("status"),
                "entities": memory.get("entities") or [],
                "tags": memory.get("tags") or [],
                "embedding_model": memory.get("embedding_model"),
                "vector": {
                    "dim": len(vector),
                    "nonzero": sum(1 for v in vector if v),
                    # A few real coordinates, so the vector is a thing on the
                    # page rather than a claim about one.
                    "sample": [round(v, 3) for v in vector[:8]],
                },
                "events": [
                    {"event": e["event"], "reason": e["reason"]}
                    for e in store.events_for(memory["id"])
                ],
                "replaced": (
                    {
                        "id": replaced["id"],
                        "content": replaced["content"],
                        "created_at": replaced["created_at"],
                    }
                    if replaced
                    else None
                ),
            }
        )

    return {
        "transcript": {
            "id": transcript_id,
            "raw_asr": transcript.get("raw_asr"),
            "formatted_text": transcript.get("formatted_text"),
            "application": transcript.get("application"),
            "timestamp": transcript.get("timestamp"),
        },
        "heard_differently": _asr_differences(
            transcript.get("raw_asr") or "", transcript.get("formatted_text") or ""
        ),
        "extraction": store.extraction_run_for(transcript_id),
        "memories": out,
    }


def _example_score(row: Any) -> tuple[int, int, int]:
    """How much of the pipeline one dictation can demonstrate.

    Ranked by what a reader learns from it, not by recency. A dictation that
    corrected an earlier belief shows reconciliation, which is the part of the
    system hardest to believe from prose. One where the recogniser and the
    formatted text disagree shows why the two passes are both kept - the whole
    entity path downstream depends on the second one. More memories from one
    dictation shows that extraction is not one-in-one-out.
    """
    corrections = int(row["corrections"] or 0)
    misheard = 1 if _asr_differences(row["raw_asr"] or "", row["formatted_text"] or "") else 0
    return (min(corrections, 1), misheard, int(row["memories"] or 0))


def _asr_differences(raw: str, formatted: str) -> list[dict[str, str]]:
    """Where the recogniser and the formatted text disagree, word by word.

    The corpus keeps both passes, and the difference between them is the most
    concrete illustration of what Kivi is for: a recogniser writes *rahool*,
    formatting writes *Rahul*, and everything downstream - the entity bonus, the
    name filter, the answer - depends on the second one.
    """
    strip = str.maketrans("", "", ".,!?;:'\"")
    a = [w.translate(strip).lower() for w in raw.split()]
    b_raw = formatted.split()
    b = [w.translate(strip).lower() for w in b_raw]

    out: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            continue
        out.append(
            {
                "kind": tag,
                "heard": " ".join(a[i1:i2]),
                "written": " ".join(b_raw[j1:j2]),
            }
        )
    return out


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
