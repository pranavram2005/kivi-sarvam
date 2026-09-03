"""Import a corpus of dictations into Kivi.

    python scripts/import_corpus.py data/development_corpus.jsonl
    python scripts/import_corpus.py reviewer_data.jsonl --reset
    python scripts/import_corpus.py reviewer_data.jsonl --process

Accepts JSON Lines (one record per line) or a single JSON array.
The record format is documented in `docs/CORPUS_FORMAT.md`:

    {
      "id":               "string, optional but recommended",
      "raw_asr":          "string",
      "formatted_output": "string",     (or "formatted_text")
      "timestamp":        "ISO-8601",
      "application":      "string, optional",
      "metadata":         { }           optional
    }

Records are validated before anything is written, and every problem is reported
with its line number. Importing the same id twice updates that record rather
than creating a duplicate, so a re-run is safe.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from backend.config import get_settings
from backend.database.db import init_db, reset_db
from backend.memory import store


class RecordError(Exception):
    pass


def _validate(record: dict, line_number: int) -> dict:
    """Check one record and normalise it to the internal shape."""
    if not isinstance(record, dict):
        raise RecordError(f"line {line_number}: expected a JSON object, got {type(record).__name__}")

    formatted = record.get("formatted_output") or record.get("formatted_text") or ""
    raw = record.get("raw_asr") or ""

    if not str(formatted).strip() and not str(raw).strip():
        raise RecordError(
            f"line {line_number}: needs at least one of 'formatted_output' or 'raw_asr'"
        )

    timestamp = str(record.get("timestamp") or "").strip()
    if timestamp:
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            raise RecordError(
                f"line {line_number}: 'timestamp' is not ISO-8601 ({timestamp!r}). "
                f"Expected something like 2026-08-20T09:30:00"
            )
    else:
        raise RecordError(f"line {line_number}: 'timestamp' is required")

    metadata = record.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise RecordError(f"line {line_number}: 'metadata' must be an object")

    return {
        "external_id": str(record.get("id") or f"line_{line_number}"),
        "raw_asr": str(raw or formatted),
        "formatted_text": str(formatted or raw),
        "timestamp": timestamp,
        "application": record.get("application"),
        "metadata": metadata,
    }


def load_records(path: Path) -> tuple[list[dict], list[str]]:
    """Parse a .jsonl or .json file into validated records plus any errors."""
    if not path.exists():
        raise SystemExit(f"No such file: {path}")

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"{path} is empty.")

    records: list[dict] = []
    errors: list[str] = []

    if text.lstrip().startswith("["):
        try:
            items = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path} is not valid JSON: {exc}")
        for position, item in enumerate(items, start=1):
            try:
                records.append(_validate(item, position))
            except RecordError as exc:
                errors.append(str(exc))
    else:
        for number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {number}: not valid JSON ({exc.msg})")
                continue
            try:
                records.append(_validate(item, number))
            except RecordError as exc:
                errors.append(str(exc))

    return records, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Import dictations into Kivi.")
    parser.add_argument("path", type=Path, help="A .jsonl or .json corpus file.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete all existing data first (transcripts, memories, logs).",
    )
    parser.add_argument(
        "--process",
        action="store_true",
        help="Run memory extraction immediately after importing.",
    )
    parser.add_argument("--user", default=None, help="Override the user id.")
    args = parser.parse_args()

    settings = get_settings()
    user_id = args.user or settings.default_user_id

    if args.reset:
        print("Resetting the database...")
        try:
            reset_db()
        except RuntimeError as exc:
            raise SystemExit(str(exc))
    else:
        init_db()

    records, errors = load_records(args.path)

    if errors:
        print(f"\n{len(errors)} record(s) could not be read:")
        for message in errors[:25]:
            print(f"  - {message}")
        if len(errors) > 25:
            print(f"  ... and {len(errors) - 25} more")

    if not records:
        print("\nNothing to import.")
        return 1

    print(f"\nImporting {len(records)} record(s) as user {user_id!r}...")
    imported = 0
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
        imported += 1

    total = store.count_transcripts(user_id)
    pending = len(store.unprocessed_transcripts(user_id=user_id))
    print(f"Imported {imported}. The database now holds {total} transcript(s).")
    print(f"{pending} transcript(s) are waiting for memory extraction.")

    if args.process:
        print()
        from scripts.process_corpus import run_processing  # local import, keeps startup light

        run_processing(user_id=user_id)
    else:
        print("\nNext:  python scripts/process_corpus.py")

    return 0 if not errors else 0


if __name__ == "__main__":
    sys.exit(main())
