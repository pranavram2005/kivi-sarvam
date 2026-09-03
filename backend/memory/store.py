"""Persistence for transcripts, memories, provenance and query logs.

Every write that changes what Kivi believes also writes a `memory_events` row.
That table is the answer to "why does this memory look like this?", and it is
what makes the Inspector screen honest rather than decorative.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from backend.database.db import get_connection, pack_vector, row_to_dict, rows_to_dicts, unpack_vector

ACTIVE = "ACTIVE"
SUPERSEDED = "SUPERSEDED"
DELETED = "DELETED"
REJECTED = "REJECTED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    return conn or get_connection()


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------
def insert_transcript(
    *,
    user_id: str,
    raw_asr: str,
    formatted_text: str,
    timestamp: str,
    application: str | None = None,
    metadata: dict[str, Any] | None = None,
    external_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Store a dictation. Re-importing the same external id updates in place."""
    connection = _conn(conn)
    cursor = connection.execute(
        """
        INSERT INTO transcripts
            (user_id, external_id, raw_asr, formatted_text, application, timestamp, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (user_id, external_id) DO UPDATE SET
            raw_asr        = excluded.raw_asr,
            formatted_text = excluded.formatted_text,
            application    = excluded.application,
            timestamp      = excluded.timestamp,
            metadata       = excluded.metadata
        RETURNING id
        """,
        (
            user_id,
            external_id,
            raw_asr,
            formatted_text,
            application,
            timestamp,
            json.dumps(metadata or {}),
        ),
    )
    transcript_id = int(cursor.fetchone()[0])
    connection.commit()
    return transcript_id


def get_transcript(transcript_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    row = _conn(conn).execute("SELECT * FROM transcripts WHERE id = ?", (transcript_id,)).fetchone()
    return row_to_dict(row)


def list_transcripts(
    *,
    user_id: str,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    application: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    sql = ["SELECT * FROM transcripts WHERE user_id = ? AND id NOT IN "
           "(SELECT transcript_id FROM transcript_deletions)"]
    params: list[Any] = [user_id]
    if search:
        sql.append("AND (formatted_text LIKE ? OR raw_asr LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if application:
        sql.append("AND application = ?")
        params.append(application)
    sql.append("ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?")
    params += [limit, offset]
    rows = _conn(conn).execute(" ".join(sql), params).fetchall()
    return rows_to_dicts(rows)


def load_searchable_transcripts(
    *,
    user_id: str,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Every dictation, for the retrieval fallback.

    `list_transcripts(search=...)` does a substring LIKE, which is right for the
    History screen's find-as-you-type and useless for answering a question:
    "how should my release notes read" shares no substring with "keep my release
    notes plain". The fallback needs ranking, so it loads the text and scores it
    with the same BM25 used for memories.
    """
    rows = _conn(conn).execute(
        """
        SELECT id, formatted_text, raw_asr, application, timestamp, processed_at
        FROM transcripts WHERE user_id = ?
          AND id NOT IN (SELECT transcript_id FROM transcript_deletions)
        ORDER BY timestamp DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    return rows_to_dicts(rows)


def count_transcripts(user_id: str, conn: sqlite3.Connection | None = None) -> int:
    row = _conn(conn).execute(
        "SELECT COUNT(*) FROM transcripts WHERE user_id = ? AND id NOT IN "
        "(SELECT transcript_id FROM transcript_deletions)", (user_id,)
    ).fetchone()
    return int(row[0])


def unprocessed_transcripts(
    *, user_id: str, limit: int | None = None, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT * FROM transcripts WHERE user_id = ? AND processed_at IS NULL "
        "ORDER BY timestamp ASC, id ASC"
    )
    params: list[Any] = [user_id]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return rows_to_dicts(_conn(conn).execute(sql, params).fetchall())


def mark_transcript_processed(transcript_id: int, conn: sqlite3.Connection | None = None) -> None:
    connection = _conn(conn)
    connection.execute(
        "UPDATE transcripts SET processed_at = ? WHERE id = ?", (_now(), transcript_id)
    )
    connection.commit()


def applications(user_id: str, conn: sqlite3.Connection | None = None) -> list[str]:
    rows = _conn(conn).execute(
        "SELECT DISTINCT application FROM transcripts WHERE user_id = ? AND application IS NOT NULL "
        "ORDER BY application",
        (user_id,),
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------
def insert_memory(
    *,
    user_id: str,
    memory_type: str,
    content: str,
    subject: str | None,
    attribute: str | None,
    value: str | None,
    entities: Sequence[str],
    tags: Sequence[str],
    confidence: float,
    status: str,
    source_transcript_id: int | None,
    occurred_at: str | None,
    embedding: Sequence[float] | None,
    embedding_model: str | None,
    conn: sqlite3.Connection | None = None,
) -> int:
    connection = _conn(conn)
    cursor = connection.execute(
        """
        INSERT INTO memories
            (user_id, type, content, subject, attribute, value, entities, tags,
             confidence, status, source_transcript_id, occurred_at,
             embedding, embedding_model, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            user_id,
            memory_type,
            content,
            subject,
            attribute,
            value,
            json.dumps(list(entities)),
            json.dumps(list(tags)),
            float(confidence),
            status,
            source_transcript_id,
            occurred_at,
            pack_vector(list(embedding)) if embedding else None,
            embedding_model,
            _now(),
            _now(),
        ),
    )
    memory_id = int(cursor.fetchone()[0])
    connection.commit()
    return memory_id


def get_memory(memory_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    row = _conn(conn).execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return row_to_dict(row)


def get_memories(ids: Sequence[int], conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = _conn(conn).execute(
        f"SELECT * FROM memories WHERE id IN ({placeholders})", list(ids)
    ).fetchall()
    by_id = {d["id"]: d for d in rows_to_dicts(rows)}
    return [by_id[i] for i in ids if i in by_id]


def list_memories(
    *,
    user_id: str,
    statuses: Sequence[str] = (ACTIVE,),
    memory_types: Sequence[str] | None = None,
    subject: str | None = None,
    search: str | None = None,
    limit: int = 500,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    sql = ["SELECT * FROM memories WHERE user_id = ?"]
    params: list[Any] = [user_id]
    if statuses:
        sql.append(f"AND status IN ({','.join('?' * len(statuses))})")
        params += list(statuses)
    if memory_types:
        sql.append(f"AND type IN ({','.join('?' * len(memory_types))})")
        params += list(memory_types)
    if subject:
        sql.append("AND LOWER(subject) = LOWER(?)")
        params.append(subject)
    if search:
        sql.append("AND (content LIKE ? OR subject LIKE ? OR entities LIKE ?)")
        params += [f"%{search}%"] * 3
    sql.append("ORDER BY datetime(updated_at) DESC, id DESC LIMIT ? OFFSET ?")
    params += [limit, offset]
    return rows_to_dicts(_conn(conn).execute(" ".join(sql), params).fetchall())


def load_retrievable(
    user_id: str, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    """Every memory a query may draw on, with its vector attached.

    ACTIVE and SUPERSEDED are both loaded: superseded memories never answer a
    question, but they let Kivi say "it was moved from 3 PM" and they make the
    correction visible in the Inspector.
    """
    rows = _conn(conn).execute(
        """
        SELECT m.*, t.timestamp AS transcript_timestamp, t.application AS application,
               t.formatted_text AS source_text
        FROM memories m
        LEFT JOIN transcripts t ON t.id = m.source_transcript_id
        WHERE m.user_id = ? AND m.status IN (?, ?)
        ORDER BY m.id
        """,
        (user_id, ACTIVE, SUPERSEDED),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        record = row_to_dict(row) or {}
        record["vector"] = unpack_vector(row["embedding"])
        out.append(record)
    return out


def slot_candidates(
    *,
    user_id: str,
    subject: str | None,
    attribute: str | None,
    memory_type: str,
    # Wide enough that a correction can still find what it corrects after a busy
    # fortnight of other meetings with the same person.
    limit: int = 15,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Active memories that might be the thing a new memory corrects.

    Matched on the (subject, attribute) slot when both are known, and on the
    subject alone otherwise - a slot match is what makes "move the meeting to
    4 PM" find the 3 PM memory instead of a random one about Rahul.

    The subject is free text produced by whatever engine did the extraction, and
    engines are not perfectly consistent about it: the same appointment can be
    filed under "Priya" one day and "Priya Vault rollout" the next. An exact
    match alone therefore loses corrections silently - the new memory simply
    lands beside the old one and both stay active. So an exact match is tried
    first, and a containment match second, which catches subject drift in either
    direction while staying scoped to the same attribute. The engine's `resolve`
    still makes the final call on anything this turns up.
    """
    connection = _conn(conn)
    if subject and attribute:
        rows = connection.execute(
            """
            SELECT m.*, t.timestamp AS timestamp,
                   t.formatted_text AS source_sentence
            FROM memories m LEFT JOIN transcripts t ON t.id = m.source_transcript_id
            WHERE m.user_id = ? AND m.status = ? AND LOWER(m.subject) = LOWER(?)
              AND LOWER(COALESCE(m.attribute,'')) = LOWER(?)
            ORDER BY datetime(m.created_at) DESC, m.id DESC LIMIT ?
            """,
            (user_id, ACTIVE, subject, attribute, limit),
        ).fetchall()
        if rows:
            return rows_to_dicts(rows)

        # Same slot, drifted subject: "Priya" vs "Priya Vault rollout".
        rows = connection.execute(
            """
            SELECT m.*, t.timestamp AS timestamp,
                   t.formatted_text AS source_sentence
            FROM memories m LEFT JOIN transcripts t ON t.id = m.source_transcript_id
            WHERE m.user_id = ? AND m.status = ?
              AND LOWER(COALESCE(m.attribute,'')) = LOWER(?)
              AND m.subject IS NOT NULL AND m.subject <> ''
              AND (
                    INSTR(LOWER(?), LOWER(m.subject)) > 0
                 OR INSTR(LOWER(m.subject), LOWER(?)) > 0
              )
            ORDER BY datetime(m.created_at) DESC, m.id DESC LIMIT ?
            """,
            (user_id, ACTIVE, attribute, subject, subject, limit),
        ).fetchall()
        if rows:
            return rows_to_dicts(rows)

    if subject:
        rows = connection.execute(
            """
            SELECT m.*, t.timestamp AS timestamp,
                   t.formatted_text AS source_sentence
            FROM memories m LEFT JOIN transcripts t ON t.id = m.source_transcript_id
            WHERE m.user_id = ? AND m.status = ? AND LOWER(m.subject) = LOWER(?)
              AND m.type = ?
            ORDER BY datetime(m.created_at) DESC, m.id DESC LIMIT ?
            """,
            (user_id, ACTIVE, subject, memory_type, limit),
        ).fetchall()
        return rows_to_dicts(rows)

    return []


def update_memory(
    memory_id: int,
    *,
    fields: dict[str, Any],
    embedding: Sequence[float] | None = None,
    embedding_model: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    allowed = {
        "type",
        "content",
        "subject",
        "attribute",
        "value",
        "confidence",
        "status",
        "occurred_at",
        "superseded_by_id",
    }
    assignments: list[str] = []
    params: list[Any] = []

    for key, val in fields.items():
        if key in allowed:
            assignments.append(f"{key} = ?")
            params.append(val)
        elif key in ("entities", "tags"):
            assignments.append(f"{key} = ?")
            params.append(json.dumps(list(val or [])))

    if embedding is not None:
        assignments.append("embedding = ?")
        params.append(pack_vector(list(embedding)))
        assignments.append("embedding_model = ?")
        params.append(embedding_model)

    if not assignments:
        return get_memory(memory_id, conn)

    assignments.append("updated_at = ?")
    params.append(_now())
    params.append(memory_id)

    connection = _conn(conn)
    connection.execute(f"UPDATE memories SET {', '.join(assignments)} WHERE id = ?", params)
    connection.commit()
    return get_memory(memory_id, connection)


def set_status(
    memory_id: int,
    status: str,
    *,
    superseded_by_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    connection = _conn(conn)
    connection.execute(
        "UPDATE memories SET status = ?, superseded_by_id = ?, updated_at = ? WHERE id = ?",
        (status, superseded_by_id, _now(), memory_id),
    )
    connection.commit()


def memory_counts(user_id: str, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    rows = _conn(conn).execute(
        "SELECT status, COUNT(*) AS n FROM memories WHERE user_id = ? GROUP BY status",
        (user_id,),
    ).fetchall()
    return {row["status"]: int(row["n"]) for row in rows}


def memory_type_counts(user_id: str, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    rows = _conn(conn).execute(
        "SELECT type, COUNT(*) AS n FROM memories WHERE user_id = ? AND status = ? GROUP BY type",
        (user_id, ACTIVE),
    ).fetchall()
    return {row["type"]: int(row["n"]) for row in rows}


def known_entities(user_id: str, conn: sqlite3.Connection | None = None) -> list[str]:
    """Every person and project Kivi has heard of, for query understanding."""
    rows = _conn(conn).execute(
        "SELECT subject, entities FROM memories WHERE user_id = ? AND status IN (?, ?)",
        (user_id, ACTIVE, SUPERSEDED),
    ).fetchall()
    seen: dict[str, str] = {}
    for row in rows:
        if row["subject"]:
            seen.setdefault(row["subject"].lower(), row["subject"])
        try:
            for entity in json.loads(row["entities"] or "[]"):
                if isinstance(entity, str) and entity.strip():
                    seen.setdefault(entity.lower(), entity.strip())
        except (ValueError, TypeError):
            continue
    seen.pop("user", None)
    return sorted(seen.values())


# ---------------------------------------------------------------------------
# Relations and events (provenance)
# ---------------------------------------------------------------------------
def add_relation(
    *,
    memory_id: int,
    related_memory_id: int,
    relation_type: str,
    note: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    connection = _conn(conn)
    connection.execute(
        """
        INSERT INTO memory_relations (memory_id, related_memory_id, relation_type, note)
        VALUES (?, ?, ?, ?)
        """,
        (memory_id, related_memory_id, relation_type, note),
    )
    connection.commit()


def contradictions_among(
    ids: Sequence[int], conn: sqlite3.Connection | None = None
) -> dict[int, list[int]]:
    """Which of these memories are recorded as contradicting each other.

    Reconciliation already worked this out when the memories were written. It
    was not reaching the answer, so Kivi would list two flagged-contradictory
    meeting times as if they were two separate appointments.
    """
    if len(ids) < 2:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = _conn(conn).execute(
        f"""
        SELECT memory_id, related_memory_id FROM memory_relations
        WHERE relation_type = 'CONTRADICTS'
          AND memory_id IN ({placeholders}) AND related_memory_id IN ({placeholders})
        """,
        list(ids) + list(ids),
    ).fetchall()

    pairs: dict[int, list[int]] = {}
    for r in rows:
        a, b = int(r["memory_id"]), int(r["related_memory_id"])
        pairs.setdefault(a, []).append(b)
        pairs.setdefault(b, []).append(a)
    return pairs


def relations_for(memory_id: int, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    rows = _conn(conn).execute(
        """
        SELECT r.*, m.content AS related_content, m.status AS related_status,
               m.type AS related_type
        FROM memory_relations r
        JOIN memories m ON m.id = r.related_memory_id
        WHERE r.memory_id = ?
        UNION ALL
        SELECT r.*, m.content AS related_content, m.status AS related_status,
               m.type AS related_type
        FROM memory_relations r
        JOIN memories m ON m.id = r.memory_id
        WHERE r.related_memory_id = ?
        ORDER BY created_at DESC
        """,
        (memory_id, memory_id),
    ).fetchall()
    return rows_to_dicts(rows)


def log_event(
    *,
    memory_id: int | None,
    transcript_id: int | None,
    event: str,
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
    actor: str = "system",
    conn: sqlite3.Connection | None = None,
) -> None:
    connection = _conn(conn)
    connection.execute(
        """
        INSERT INTO memory_events (memory_id, transcript_id, event, reason, detail, actor)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (memory_id, transcript_id, event, reason, json.dumps(detail or {}), actor),
    )
    connection.commit()


def events_for(memory_id: int, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    rows = _conn(conn).execute(
        "SELECT * FROM memory_events WHERE memory_id = ? ORDER BY id ASC", (memory_id,)
    ).fetchall()
    return rows_to_dicts(rows)


def recent_events(
    user_id: str, limit: int = 50, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    rows = _conn(conn).execute(
        """
        SELECT e.*, m.content AS memory_content, m.type AS memory_type
        FROM memory_events e
        LEFT JOIN memories m ON m.id = e.memory_id
        WHERE m.user_id = ? OR e.memory_id IS NULL
        ORDER BY e.id DESC LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Extraction runs
# ---------------------------------------------------------------------------
def log_extraction_run(
    *,
    transcript_id: int,
    provider: str,
    model: str,
    decision: str,
    rationale: str,
    created: int,
    rejected: int,
    superseded: int,
    duplicate: int,
    raw_response: str | None,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    cost_usd: float,
    conn: sqlite3.Connection | None = None,
) -> int:
    connection = _conn(conn)
    cursor = connection.execute(
        """
        INSERT INTO extraction_runs
            (transcript_id, provider, model, decision, rationale, memories_created,
             memories_rejected, memories_superseded, memories_duplicate, raw_response,
             input_tokens, output_tokens, latency_ms, cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            transcript_id,
            provider,
            model,
            decision,
            rationale,
            created,
            rejected,
            superseded,
            duplicate,
            raw_response,
            input_tokens,
            output_tokens,
            latency_ms,
            cost_usd,
        ),
    )
    run_id = int(cursor.fetchone()[0])
    connection.commit()
    return run_id


def extraction_run_for(
    transcript_id: int, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    row = _conn(conn).execute(
        "SELECT * FROM extraction_runs WHERE transcript_id = ? ORDER BY id DESC LIMIT 1",
        (transcript_id,),
    ).fetchone()
    return row_to_dict(row)


def extraction_totals(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    row = _conn(conn).execute(
        """
        SELECT COUNT(*)                          AS runs,
               SUM(decision = 'REMEMBER')        AS remembered,
               SUM(decision = 'IGNORE')          AS ignored,
               COALESCE(SUM(memories_created),0) AS created,
               COALESCE(SUM(memories_rejected),0) AS rejected,
               COALESCE(SUM(memories_superseded),0) AS superseded,
               COALESCE(SUM(memories_duplicate),0) AS duplicate,
               COALESCE(SUM(input_tokens),0)     AS input_tokens,
               COALESCE(SUM(output_tokens),0)    AS output_tokens,
               COALESCE(SUM(cost_usd),0)         AS cost_usd,
               COALESCE(AVG(latency_ms),0)       AS avg_latency_ms
        FROM extraction_runs
        """
    ).fetchone()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Query logs
# ---------------------------------------------------------------------------
def log_query(
    *,
    user_id: str,
    question: str,
    answer: str,
    abstained: bool,
    conflict: bool,
    supported: bool,
    confidence: float,
    reasoning: str,
    retrieved_memory_ids: Sequence[int],
    used_memory_ids: Sequence[int],
    retrieval_detail: list[dict[str, Any]],
    provider: str,
    model: str,
    retrieval_latency_ms: float,
    llm_latency_ms: float,
    total_latency_ms: float,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    conn: sqlite3.Connection | None = None,
) -> int:
    connection = _conn(conn)
    cursor = connection.execute(
        """
        INSERT INTO query_logs
            (user_id, question, answer, abstained, conflict, supported, confidence, reasoning,
             retrieved_memory_ids, used_memory_ids, retrieval_detail, provider, model,
             retrieval_latency_ms, llm_latency_ms, total_latency_ms,
             input_tokens, output_tokens, cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            user_id,
            question,
            answer,
            int(abstained),
            int(conflict),
            int(supported),
            float(confidence),
            reasoning,
            json.dumps(list(retrieved_memory_ids)),
            json.dumps(list(used_memory_ids)),
            json.dumps(retrieval_detail),
            provider,
            model,
            retrieval_latency_ms,
            llm_latency_ms,
            total_latency_ms,
            input_tokens,
            output_tokens,
            cost_usd,
        ),
    )
    query_id = int(cursor.fetchone()[0])
    connection.commit()
    return query_id


def get_query_log(query_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    row = _conn(conn).execute("SELECT * FROM query_logs WHERE id = ?", (query_id,)).fetchone()
    return row_to_dict(row)


def list_query_logs(
    *, user_id: str, limit: int = 50, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    rows = _conn(conn).execute(
        "SELECT * FROM query_logs WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    ).fetchall()
    return rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Evaluation runs
# ---------------------------------------------------------------------------
def create_eval_run(
    *,
    started_at: str,
    provider: str,
    model: str,
    embedding_provider: str,
    conn: sqlite3.Connection | None = None,
) -> int:
    connection = _conn(conn)
    cursor = connection.execute(
        """
        INSERT INTO eval_runs (started_at, provider, model, embedding_provider)
        VALUES (?, ?, ?, ?) RETURNING id
        """,
        (started_at, provider, model, embedding_provider),
    )
    run_id = int(cursor.fetchone()[0])
    connection.commit()
    return run_id


def finish_eval_run(
    *,
    run_id: int,
    finished_at: str,
    total_cases: int,
    metrics: dict[str, Any],
    notes: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    connection = _conn(conn)
    connection.execute(
        "UPDATE eval_runs SET finished_at = ?, total_cases = ?, metrics = ?, notes = ? WHERE id = ?",
        (finished_at, total_cases, json.dumps(metrics), notes, run_id),
    )
    connection.commit()


def add_eval_result(
    *,
    run_id: int,
    case_id: str,
    category: str,
    passed: bool,
    detail: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> None:
    connection = _conn(conn)
    connection.execute(
        "INSERT INTO eval_results (run_id, case_id, category, passed, detail) VALUES (?, ?, ?, ?, ?)",
        (run_id, case_id, category, int(passed), json.dumps(detail)),
    )
    connection.commit()


def latest_eval_run(conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    row = _conn(conn).execute(
        "SELECT * FROM eval_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row_to_dict(row)


def eval_results_for(run_id: int, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    rows = _conn(conn).execute(
        "SELECT * FROM eval_results WHERE run_id = ? ORDER BY id ASC", (run_id,)
    ).fetchall()
    return rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Deleting a dictation
# ---------------------------------------------------------------------------
def delete_transcript(
    transcript_id: int,
    *,
    reason: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[int]:
    """Hide a dictation and forget what it taught Kivi.

    Returns the ids of the memories that were forgotten with it.

    The row is not removed. `memories.source_transcript_id` is ON DELETE
    CASCADE, so deleting it would take its memories and their audit events
    with it, and answers already in the query log would point at rows that no
    longer exist. Provenance is the property the system exists to guarantee,
    so a deleted dictation is hidden and its memories are marked DELETED -
    the same treatment a forgotten memory gets, and reversible the same way.

    Memories that are already SUPERSEDED or REJECTED are left alone: they are
    not visible anyway, and restoring the dictation should not resurrect a
    belief that something later corrected.
    """
    connection = _conn(conn)
    affected = [
        int(r[0])
        for r in connection.execute(
            "SELECT id FROM memories WHERE source_transcript_id = ? AND status = 'ACTIVE'",
            (transcript_id,),
        ).fetchall()
    ]
    now = _now()
    connection.execute(
        "INSERT OR REPLACE INTO transcript_deletions (transcript_id, deleted_at, reason) "
        "VALUES (?, ?, ?)",
        (transcript_id, now, reason),
    )
    for memory_id in affected:
        connection.execute(
            "UPDATE memories SET status = 'DELETED', updated_at = ? WHERE id = ?",
            (now, memory_id),
        )
    connection.commit()
    return affected


def restore_transcript(
    transcript_id: int, *, conn: sqlite3.Connection | None = None
) -> list[int]:
    """Bring a deleted dictation back, and with it the memories it produced."""
    connection = _conn(conn)
    affected = [
        int(r[0])
        for r in connection.execute(
            "SELECT id FROM memories WHERE source_transcript_id = ? AND status = 'DELETED'",
            (transcript_id,),
        ).fetchall()
    ]
    connection.execute(
        "DELETE FROM transcript_deletions WHERE transcript_id = ?", (transcript_id,)
    )
    now = _now()
    for memory_id in affected:
        connection.execute(
            "UPDATE memories SET status = 'ACTIVE', updated_at = ? WHERE id = ?",
            (now, memory_id),
        )
    connection.commit()
    return affected


def is_transcript_deleted(
    transcript_id: int, *, conn: sqlite3.Connection | None = None
) -> bool:
    row = _conn(conn).execute(
        "SELECT 1 FROM transcript_deletions WHERE transcript_id = ?", (transcript_id,)
    ).fetchone()
    return row is not None
