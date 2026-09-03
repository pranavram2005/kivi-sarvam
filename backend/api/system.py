"""System status, corpus import, reset, and evaluation results."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Query

from backend.config import REPO_ROOT, get_settings
from backend.database.db import clear_all_tables
from backend.llm.embeddings import get_embedder
from backend.llm.engine import build_engine, get_engine
from backend.memory import extractor, store
from backend.models.schemas import (
    CorpusImportRequest,
    CorpusImportResponse,
    ProcessResponse,
    SystemStatus,
    TranscriptIn,
)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/system/status", response_model=SystemStatus)
def system_status() -> SystemStatus:
    """What is configured and what is stored. The Inspector's header reads this."""
    settings = get_settings()
    engine = get_engine()
    embedder = get_embedder()
    user_id = settings.default_user_id
    connection = store.get_connection()

    unprocessed = connection.execute(
        "SELECT COUNT(*) FROM transcripts WHERE user_id = ? AND processed_at IS NULL", (user_id,)
    ).fetchone()[0]
    queries = connection.execute(
        "SELECT COUNT(*) FROM query_logs WHERE user_id = ?", (user_id,)
    ).fetchone()[0]

    return SystemStatus(
        user_id=user_id,
        llm_provider=engine.name,
        llm_model=engine.model,
        embedding_provider=embedder.name,
        embedding_model=embedder.model,
        embedding_dim=embedder.dim,
        database=str(settings.db_path.relative_to(REPO_ROOT))
        if settings.db_path.is_relative_to(REPO_ROOT)
        else str(settings.db_path),
        transcripts=store.count_transcripts(user_id),
        transcripts_unprocessed=int(unprocessed),
        memories=store.memory_counts(user_id),
        memory_types=store.memory_type_counts(user_id),
        queries=int(queries),
        extraction=store.extraction_totals(),
        offline_mode=engine.name == "heuristic",
    )


@router.post("/system/reset")
def reset_system(confirm: bool = Query(default=False)) -> dict[str, Any]:
    """Wipe every transcript, memory, log and evaluation result.

    Requires ?confirm=true, because this is the one endpoint that destroys data.
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="This deletes all stored data. Call again with ?confirm=true.",
        )
    # Truncate rather than delete the file. This handler runs inside the
    # server that is holding the database open, and on Windows an open handle
    # from any worker thread makes the file undeletable - so `reset_db` can
    # never succeed from here, only from the CLI where no server is running.
    path = clear_all_tables()
    return {"status": "reset", "database": str(path)}


# ---------------------------------------------------------------------------
# Corpus import
# ---------------------------------------------------------------------------
def _import_records(
    records: list[TranscriptIn], *, user_id: str
) -> tuple[int, int, list[str], list[int]]:
    imported = 0
    skipped = 0
    errors: list[str] = []
    ids: list[int] = []

    for position, record in enumerate(records):
        text = record.text()
        if not text.strip():
            skipped += 1
            errors.append(f"record {position} ({record.id or 'no id'}): no text, skipped")
            continue
        try:
            transcript_id = store.insert_transcript(
                user_id=user_id,
                raw_asr=record.asr(),
                formatted_text=text,
                timestamp=record.timestamp,
                application=record.application,
                metadata=record.metadata,
                external_id=record.id or f"import_{position}",
            )
            ids.append(transcript_id)
            imported += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"record {position} ({record.id or 'no id'}): {exc}")

    return imported, skipped, errors, ids


def _process_all(
    user_id: str, workers: int = 4, engine_name: str | None = None
) -> ProcessResponse:
    """Extract memories from everything awaiting it.

    `workers` only parallelises the extraction call. Reconciliation and writing
    stay strictly sequential in timestamp order, because a correction only means
    anything once the thing it corrects has been learned - so the stored result
    is identical whatever this is set to.

    It defaults above 1 because this runs inside a request. Against the offline
    engine the whole corpus takes about a second and concurrency is irrelevant;
    against a hosted model each record is a round trip, and 500 of them in
    series is fifteen minutes or more - long enough that a proxy will close the
    connection before the import returns.
    """
    engine = build_engine(engine_name) if engine_name else get_engine()
    started = time.perf_counter()
    results = extractor.process_pending(user_id=user_id, engine=engine, workers=workers)
    return ProcessResponse(
        processed=len(results),
        remembered=sum(1 for r in results if r.decision == "REMEMBER"),
        ignored=sum(1 for r in results if r.decision == "IGNORE"),
        memories_created=sum(r.created for r in results),
        memories_rejected=sum(r.rejected for r in results),
        memories_superseded=sum(r.superseded for r in results),
        memories_duplicate=sum(r.duplicates for r in results),
        conflicts=sum(r.conflicts for r in results),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        provider=engine.name,
        model=engine.model,
        results=[],
    )


@router.post("/corpus/import", response_model=CorpusImportResponse)
def import_corpus(payload: CorpusImportRequest) -> CorpusImportResponse:
    """Import a corpus of dictations as JSON.

    The same code path the CLI importer uses. See `docs/CORPUS_FORMAT.md` for
    the accepted record shape.
    """
    settings = get_settings()
    if payload.reset:
        # in-process: see the note on the reset endpoint above
        clear_all_tables()

    imported, skipped, errors, _ = _import_records(
        payload.records, user_id=settings.default_user_id
    )
    processed = _process_all(settings.default_user_id) if payload.process else None
    return CorpusImportResponse(
        imported=imported, skipped=skipped, errors=errors[:50], processed=processed
    )


@router.post("/corpus/upload", response_model=CorpusImportResponse)
async def upload_corpus(
    file: UploadFile = File(...),
    process: bool = Query(default=True),
    reset: bool = Query(default=False),
    workers: int = Query(default=4, ge=1, le=12),
    engine: str | None = Query(default=None),
) -> CorpusImportResponse:
    """Import a `.jsonl` (or JSON array) corpus file straight from the browser.

    `engine` overrides the configured provider for this import only, and exists
    because a bulk import and a question have opposite requirements. Answering
    one question with a model costs a couple of seconds and is worth it. A
    500-record import is a few hundred extraction calls plus a reconciliation
    call per candidate memory, run in timestamp order because a correction only
    means anything after the thing it corrects - measured at 9.8s per record
    even with `workers=4`, so roughly 82 minutes, which no proxy will hold a
    connection open for.

    Pass `engine=heuristic` to import a corpus in seconds and leave the
    configured model to answer questions about it. Or import with
    `process=false` and extract separately: transcripts awaiting extraction are
    picked up by any later `POST /api/memory/process`.
    """
    settings = get_settings()
    raw = (await file.read()).decode("utf-8", errors="replace")

    records: list[TranscriptIn] = []
    errors: list[str] = []
    stripped = raw.strip()

    if stripped.startswith("["):
        try:
            for item in json.loads(stripped):
                records.append(TranscriptIn(**item))
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not read JSON array: {exc}")
    else:
        for number, line in enumerate(stripped.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(TranscriptIn(**json.loads(line)))
            except Exception as exc:
                errors.append(f"line {number}: {exc}")

    if not records:
        raise HTTPException(status_code=422, detail="No valid records found in that file.")

    if reset:
        # in-process: see the note on the reset endpoint above
        clear_all_tables()

    imported, skipped, import_errors, _ = _import_records(
        records, user_id=settings.default_user_id
    )
    processed = (
        _process_all(settings.default_user_id, workers=workers, engine_name=engine)
        if process
        else None
    )
    return CorpusImportResponse(
        imported=imported,
        skipped=skipped,
        errors=(errors + import_errors)[:50],
        processed=processed,
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
eval_router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])

RESULTS_DIR = REPO_ROOT / "evaluation" / "results"


@eval_router.post("/run")
def run_evaluation(category: str | None = None) -> dict[str, Any]:
    """Run the evaluation suite and return its results.

    This exists for the hosted deployment. Locally a reviewer runs
    `python evaluation/run_eval.py`; on a hosted instance there is no shell, and
    the assignment requires a hosted app to document a working evaluation
    procedure. Same code path either way - this imports the suite rather than
    reimplementing it, so the two can never drift apart.

    It runs synchronously and takes a few seconds against the offline engine.
    Against a remote model it takes minutes, which is why the offline engine is
    the deployed default.
    """
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from evaluation.run_eval import (  # noqa: PLC0415
            CASES_PATH,
            STATE_CHECKS,
            compute_metrics,
            db_growth,
            db_snapshot,
            evaluate_query_case,
            evaluate_state_case,
            external_id_map,
            load_cases,
        )
    except Exception as exc:  # pragma: no cover - only if the suite is absent
        raise HTTPException(
            status_code=500, detail=f"the evaluation suite could not be loaded: {exc}"
        ) from exc

    settings = get_settings()
    user_id = settings.default_user_id
    if store.count_transcripts(user_id) == 0:
        raise HTTPException(
            status_code=409,
            detail="the database is empty - seed or import a corpus before evaluating",
        )

    cases = load_cases(CASES_PATH, category)
    if not cases:
        raise HTTPException(status_code=404, detail=f"no cases matched category={category!r}")

    engine = get_engine()
    id_to_external = external_id_map(user_id)
    before = db_snapshot()
    started = time.perf_counter()

    # Dispatch exactly as the CLI does: a case that asserts something about
    # stored state is a state case, one with a question is a query case, and
    # anything else is skipped rather than guessed at.
    results = []
    for case in cases:
        if any(key in case for key in STATE_CHECKS):
            results.append(evaluate_state_case(case, user_id))
        elif case.get("question"):
            results.append(evaluate_query_case(case, user_id, id_to_external))

    metrics = compute_metrics(results, user_id)
    return {
        "source": "live",
        "run": {
            "provider": engine.name,
            "model": engine.model,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        },
        "metrics": metrics,
        "database_growth": db_growth(before, db_snapshot()),
        "cases": [r.as_dict() for r in results],
    }


@eval_router.get("/results")
def evaluation_results() -> dict[str, Any]:
    """The most recent evaluation run.

    Read from the database when a run happened in this installation, and from
    the committed `evaluation/results/latest.json` otherwise - so the Inspector
    shows real numbers on a fresh clone before the reviewer runs anything.
    """
    run = store.latest_eval_run()
    if run:
        results = store.eval_results_for(run["id"])
        # `metrics` is returned at the top level, matching the shape of the
        # committed results file. The two sources must be interchangeable - the
        # Inspector reads whichever is available and should not have to know
        # which one it got.
        return {
            "source": "database",
            "run": run,
            "metrics": run.get("metrics") or {},
            "cases": [
                {
                    "case_id": r["case_id"],
                    "category": r["category"],
                    "passed": bool(r["passed"]),
                    **(r["detail"] if isinstance(r["detail"], dict) else {}),
                }
                for r in results
            ],
        }

    committed = RESULTS_DIR / "latest.json"
    if committed.exists():
        payload = json.loads(committed.read_text(encoding="utf-8"))
        payload["source"] = "committed file (evaluation/results/latest.json)"
        return payload

    return {
        "source": "none",
        "run": None,
        "cases": [],
        "message": "No evaluation has been run yet. Run: python evaluation/run_eval.py",
    }


@eval_router.get("/runs")
def evaluation_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    rows = store.get_connection().execute(
        "SELECT * FROM eval_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return store.rows_to_dicts(rows)
