"""Run memory extraction over every transcript that has not been processed.

    python scripts/process_corpus.py
    python scripts/process_corpus.py --reprocess-all
    python scripts/process_corpus.py --limit 50

Transcripts are processed oldest first, because a correction can only supersede
something Kivi already learned.
"""

from __future__ import annotations

import argparse
import sys
import time

import _bootstrap  # noqa: F401

from backend.config import get_settings
from backend.database.db import init_db
from backend.llm.engine import get_engine
from backend.memory import extractor, store


def run_processing(
    *, user_id: str, limit: int | None = None, quiet: bool = False, workers: int = 1
) -> dict:
    """Process pending transcripts, printing a progress line as it goes."""
    engine = get_engine()
    pending = store.unprocessed_transcripts(user_id=user_id, limit=limit)

    if not pending:
        print("Nothing to process - every transcript has already been through extraction.")
        print("Use --reprocess-all to run them again.")
        return {}

    note = f" ({workers} concurrent extractions)" if workers > 1 else ""
    print(
        f"Processing {len(pending)} transcript(s) with the {engine.name} engine "
        f"({engine.model}){note}.",
        flush=True,
    )
    started = time.perf_counter()

    totals = {
        "processed": 0,
        "remembered": 0,
        "ignored": 0,
        "created": 0,
        "rejected": 0,
        "superseded": 0,
        "duplicate": 0,
        "conflicts": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }

    def progress(index: int, total: int, result) -> None:
        totals["processed"] += 1
        totals["remembered"] += 1 if result.decision == "REMEMBER" else 0
        totals["ignored"] += 1 if result.decision == "IGNORE" else 0
        totals["created"] += result.created
        totals["rejected"] += result.rejected
        totals["superseded"] += result.superseded
        totals["duplicate"] += result.duplicates
        totals["conflicts"] += result.conflicts
        totals["input_tokens"] += result.input_tokens
        totals["output_tokens"] += result.output_tokens
        totals["cost_usd"] += result.cost_usd
        step = 25 if total > 200 else 5
        if not quiet and (index % step == 0 or index == total):
            # flush=True matters here: Python buffers stdout when it is not a
            # terminal, so a reviewer piping this to a file or watching a long
            # remote-model run would otherwise see nothing at all until the end.
            print(
                f"  {index:4d}/{total}  "
                f"remembered {totals['remembered']:4d}  "
                f"ignored {totals['ignored']:4d}  "
                f"memories {totals['created']:4d}  "
                f"superseded {totals['superseded']:3d}  "
                f"${totals['cost_usd']:.4f}",
                flush=True,
            )

    extractor.process_pending(
        user_id=user_id, limit=limit, engine=engine, progress=progress, workers=workers
    )
    elapsed = time.perf_counter() - started

    counts = store.memory_counts(user_id)
    types = store.memory_type_counts(user_id)

    print(f"\nDone in {elapsed:.1f}s ({elapsed / max(1, totals['processed']) * 1000:.0f} ms/transcript)")
    print(f"  transcripts    : {totals['processed']}")
    print(f"    remembered   : {totals['remembered']}")
    print(f"    ignored      : {totals['ignored']}   (nothing durable said)")
    print(f"  memories       : {totals['created']} created")
    print(f"    superseded   : {totals['superseded']}  (corrections applied)")
    print(f"    duplicates   : {totals['duplicate']}  (already known, not stored again)")
    print(f"    conflicts    : {totals['conflicts']}  (kept both, flagged)")
    print(f"    rejected     : {totals['rejected']}  (below the confidence threshold)")
    print(f"  memory store   : {counts}")
    print(f"  by type        : {types}")
    if totals["cost_usd"]:
        print(
            f"  model usage    : {totals['input_tokens']} in / {totals['output_tokens']} out, "
            f"${totals['cost_usd']:.4f}"
        )
    print("\nNext:  uvicorn backend.main:app --reload --reload-dir backend")
    print("       python evaluation/run_eval.py")
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract memories from stored transcripts.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N transcripts.")
    parser.add_argument(
        "--reprocess-all",
        action="store_true",
        help="Clear the processed marker and run extraction over everything again.",
    )
    parser.add_argument("--user", default=None)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Run this many model calls concurrently. Only the extraction call is "
            "parallelised; memories are still reconciled and stored strictly oldest "
            "first, so the result is identical. Worth raising for a remote model "
            "(try 6); pointless for the offline engine."
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    user_id = args.user or settings.default_user_id
    init_db()

    if args.reprocess_all:
        connection = store.get_connection()
        connection.execute(
            "UPDATE transcripts SET processed_at = NULL WHERE user_id = ?", (user_id,)
        )
        connection.commit()
        print("Cleared the processed marker on every transcript.")

    run_processing(user_id=user_id, limit=args.limit, quiet=args.quiet, workers=args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
