"""One command to get from a fresh clone to a working system.

    python scripts/seed.py

Equivalent to:
    python scripts/migrate.py
    python scripts/import_corpus.py data/development_corpus.jsonl --reset
    python scripts/process_corpus.py

Pass --keep to import on top of whatever is already stored instead of resetting.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from backend.config import REPO_ROOT, get_settings
from backend.database.db import init_db, reset_db
from backend.memory import store
from scripts.import_corpus import load_records
from scripts.process_corpus import run_processing

DEFAULT_CORPUS = REPO_ROOT / "data" / "development_corpus.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up Kivi with the development corpus.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--keep", action="store_true", help="Do not reset existing data.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    user_id = settings.default_user_id

    print("=" * 70)
    print("Kivi Semantic Memory - seeding")
    print("=" * 70)
    print(f"  database   : {settings.db_path}")
    print(f"  llm        : {settings.llm_provider} ({settings.llm_model})")
    print(f"  embeddings : {settings.embedding_provider} ({settings.embedding_model})")
    print()

    if args.keep:
        init_db()
    else:
        print("Resetting the database...")
        try:
            reset_db()
        except RuntimeError as exc:
            raise SystemExit(str(exc))

    if not args.corpus.exists():
        print(f"\nNo corpus at {args.corpus}.")
        print("Generate one with:  python scripts/generate_corpus.py")
        return 1

    records, errors = load_records(args.corpus)
    if errors:
        print(f"{len(errors)} record(s) could not be read; showing the first five:")
        for message in errors[:5]:
            print(f"  - {message}")

    print(f"Importing {len(records)} record(s) from {args.corpus.name}...")
    for record in records:
        store.insert_transcript(
            user_id=user_id,
            raw_asr=record["raw_asr"],
            formatted_text=record["formatted_text"],
            timestamp=record["timestamp"],
            application=record["application"],
            metadata=record["metadata"],
            external_id=record["external_id"],
        )
    print(f"Imported {len(records)}.\n")

    run_processing(user_id=user_id, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
