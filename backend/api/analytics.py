"""Analytics, scoped to the screen that needs them.

Each screen answers a different question, so each gets its own slice rather than
one dashboard sitting off to the side:

    /api/analytics/history   what you said      — volume, where, and how much of
                                                  it was worth keeping
    /api/analytics/memory    what Kivi believes — composition, who and what it
                                                  knows about, how it grew
    /api/analytics/queries   how Kivi answers   — honesty, speed and cost of the
                                                  questions actually asked

Every figure is computed from stored rows. Time series key on the *dictation*
timestamp rather than the database insert time: a bulk import writes 500 rows in
one second, and what a reader wants to see is the ten weeks of actual talking.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from backend.config import get_settings
from backend.memory import store

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

PROJECT_PREFIXES = ("project", "initiative", "program", "workstream")


def _week_start(iso: str) -> str | None:
    """Monday of the week containing an ISO timestamp."""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None
    return d.fromordinal(d.toordinal() - d.weekday()).isoformat()


def _weekly(user: str) -> list[dict[str, Any]]:
    """Dictations and memories kept, per week, on the dictation timeline."""
    conn = store.get_connection()

    dictations: Counter[str] = Counter()
    for r in conn.execute("SELECT timestamp FROM transcripts WHERE user_id = ?", (user,)):
        wk = _week_start(r["timestamp"])
        if wk:
            dictations[wk] += 1

    memories: Counter[str] = Counter()
    for r in conn.execute(
        """
        SELECT t.timestamp AS ts FROM memories m
        JOIN transcripts t ON t.id = m.source_transcript_id
        WHERE m.user_id = ? AND m.status IN ('ACTIVE','SUPERSEDED')
        """,
        (user,),
    ):
        wk = _week_start(r["ts"])
        if wk:
            memories[wk] += 1

    running = 0
    out = []
    for wk in sorted(set(dictations) | set(memories)):
        running += memories[wk]
        out.append(
            {
                "week": wk,
                "dictations": dictations[wk],
                "memories": memories[wk],
                "cumulative": running,
            }
        )
    return out


# ---------------------------------------------------------------------------
# History — what the user said
# ---------------------------------------------------------------------------
@router.get("/history")
def history_analytics() -> dict[str, Any]:
    user = get_settings().default_user_id
    conn = store.get_connection()
    totals = store.extraction_totals()
    runs = totals.get("runs") or 0

    by_app = [
        {
            "key": r["application"] or "unknown",
            "dictations": r["n"],
            "memories": r["m"] or 0,
            "yield": round((r["m"] or 0) / r["n"], 2) if r["n"] else 0,
        }
        for r in conn.execute(
            """
            SELECT t.application AS application, COUNT(DISTINCT t.id) n,
                   SUM(CASE WHEN m.id IS NOT NULL AND m.status IN ('ACTIVE','SUPERSEDED')
                            THEN 1 ELSE 0 END) m
            FROM transcripts t LEFT JOIN memories m ON m.source_transcript_id = t.id
            WHERE t.user_id = ?
            GROUP BY t.application ORDER BY n DESC
            """,
            (user,),
        )
    ]

    return {
        "summary": {
            "dictations": store.count_transcripts(user),
            "remembered": totals.get("remembered") or 0,
            "ignored": totals.get("ignored") or 0,
            "ignore_rate": round((totals.get("ignored") or 0) / runs, 4) if runs else None,
            "memories_created": totals.get("created") or 0,
            "memories_per_dictation": round((totals.get("created") or 0) / runs, 3) if runs else None,
            "duplicates": totals.get("duplicate") or 0,
            "unprocessed": len(store.unprocessed_transcripts(user_id=user)),
        },
        "weekly": _weekly(user),
        "by_application": by_app,
    }


# ---------------------------------------------------------------------------
# Memory — what Kivi believes
# ---------------------------------------------------------------------------
@router.get("/memory")
def memory_analytics() -> dict[str, Any]:
    user = get_settings().default_user_id
    conn = store.get_connection()
    counts = store.memory_counts(user)

    by_type = [
        {"key": r["type"], "count": r["n"]}
        for r in conn.execute(
            "SELECT type, COUNT(*) n FROM memories WHERE user_id = ? AND status='ACTIVE' "
            "GROUP BY type ORDER BY n DESC",
            (user,),
        )
    ]
    by_status = [{"key": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]

    people: Counter[str] = Counter()
    projects: Counter[str] = Counter()
    for r in conn.execute(
        "SELECT subject, entities FROM memories WHERE user_id = ? AND status='ACTIVE'", (user,)
    ):
        subject = (r["subject"] or "").strip()
        if subject and subject.lower() != "user":
            (projects if subject.lower().startswith(PROJECT_PREFIXES) else people)[subject] += 1
        try:
            for e in json.loads(r["entities"] or "[]"):
                if isinstance(e, str) and e.lower().startswith(PROJECT_PREFIXES):
                    projects[e.strip()] += 1
        except (ValueError, TypeError):
            pass

    totals = store.extraction_totals()
    return {
        "summary": {
            "active": counts.get("ACTIVE", 0),
            "superseded": counts.get("SUPERSEDED", 0),
            "rejected": counts.get("REJECTED", 0),
            "deleted": counts.get("DELETED", 0),
            "people": len(people),
            "projects": len(projects),
            "duplicates_skipped": totals.get("duplicate") or 0,
        },
        "by_type": by_type,
        "by_status": by_status,
        "top_people": [{"key": k, "count": v} for k, v in people.most_common(8)],
        "top_projects": [{"key": k, "count": v} for k, v in projects.most_common(8)],
        "weekly": _weekly(user),
    }


# ---------------------------------------------------------------------------
# Queries — how Kivi answers
# ---------------------------------------------------------------------------
@router.get("/queries")
def _signal_contributions(conn, user: str) -> dict[str, Any]:
    """How much each retrieval signal actually contributes, measured.

    `backend/memory/retriever.py` combines six signals, of which three carry a
    configurable weight and three are structural bonuses. Reading the code it is
    not obvious which dominates - the weights are visible and the bonuses are
    not, so it is easy to describe retrieval as "0.55 semantic, 0.30 lexical,
    0.15 recency" and be describing under half of it.

    Every query stores its full ranking, per signal, so this does not have to be
    argued from the source. It takes the top-ranked memory of each answered
    question - the one the score actually chose - and averages what each signal
    put into that score, with the configured weights already applied so the
    numbers are comparable.

    It is a description of the questions this installation has been asked, not a
    law about retrieval: a corpus of questions that all name a person will find
    the entity bonus dominant, because it is.
    """
    rows = conn.execute(
        "SELECT retrieval_detail FROM query_logs "
        "WHERE user_id = ? AND retrieval_detail IS NOT NULL",
        (user,),
    ).fetchall()

    settings = get_settings()
    weighted = {
        "semantic": settings.semantic_weight,
        "lexical": settings.lexical_weight,
        "recency": settings.recency_weight,
    }
    labels = {
        "semantic": "meaning",
        "lexical": "wording",
        "recency": "recency",
        "entity_bonus": "names a person or project",
        "type_bonus": "right kind of memory",
        "coverage": "covers the question's words",
    }

    totals: dict[str, float] = {k: 0.0 for k in labels}
    counted = 0
    for row in rows:
        try:
            ranking = json.loads(row["retrieval_detail"]) or []
        except (TypeError, ValueError):
            continue
        if not ranking:
            continue
        top = ranking[0]
        counted += 1
        for key in labels:
            value = float(top.get(key) or 0.0)
            totals[key] += value * weighted.get(key, 1.0)

    if not counted:
        return {"queries": 0, "signals": []}

    means = {k: v / counted for k, v in totals.items()}
    grand = sum(means.values()) or 1.0
    signals = sorted(
        (
            {
                "key": k,
                "label": labels[k],
                "mean": round(v, 4),
                "share": round(v / grand, 4),
                "weighted": k in weighted,
            }
            for k, v in means.items()
        ),
        key=lambda d: -d["mean"],
    )
    structural = sum(d["share"] for d in signals if not d["weighted"])
    return {
        "queries": counted,
        "signals": signals,
        "structural_share": round(structural, 4),
    }


def query_analytics() -> dict[str, Any]:
    user = get_settings().default_user_id
    conn = store.get_connection()

    q = conn.execute(
        """
        SELECT COUNT(*) n, SUM(abstained) ab, SUM(conflict) cf, SUM(supported) sup,
               AVG(confidence) conf, AVG(retrieval_latency_ms) rl, AVG(total_latency_ms) tl,
               SUM(input_tokens) ti, SUM(output_tokens) tout, SUM(cost_usd) cost
        FROM query_logs WHERE user_id = ?
        """,
        (user,),
    ).fetchone()
    n = q["n"] or 0

    engines = [
        {
            "provider": r["provider"] or "?",
            "model": r["model"] or "?",
            "questions": r["n"],
            "tokens": (r["tok"] or 0),
            "cost_usd": round(r["cost"] or 0, 5),
        }
        for r in conn.execute(
            "SELECT provider, model, COUNT(*) n, SUM(input_tokens+output_tokens) tok, "
            "SUM(cost_usd) cost FROM query_logs WHERE user_id = ? GROUP BY provider, model",
            (user,),
        )
    ]

    return {
        "signal_contributions": _signal_contributions(conn, user),
        "summary": {
            "total": n,
            "abstained": q["ab"] or 0,
            "conflict": q["cf"] or 0,
            "supported": q["sup"] or 0,
            "grounded": n - (q["ab"] or 0) - (q["cf"] or 0),
            "abstention_rate": round((q["ab"] or 0) / n, 4) if n else None,
            "supported_rate": round((q["sup"] or 0) / n, 4) if n else None,
            "avg_confidence": round(q["conf"] or 0, 3),
            "avg_retrieval_latency_ms": round(q["rl"] or 0, 1),
            "avg_total_latency_ms": round(q["tl"] or 0, 1),
            "tokens": (q["ti"] or 0) + (q["tout"] or 0),
            "cost_usd": round(q["cost"] or 0, 5),
        },
        "engines": engines,
    }
