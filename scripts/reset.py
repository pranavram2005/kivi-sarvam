"""Delete everything Kivi has stored and re-create an empty schema.

    python scripts/reset.py            (asks for confirmation)
    python scripts/reset.py --yes      (does not ask)

Removes every transcript, memory, provenance record, query log and evaluation
run. Files under `evaluation/results/` are left alone - they are committed
artefacts, not state; pass --results to clear those too.
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401

from backend.config import REPO_ROOT, get_settings
from backend.database.db import reset_db

RESULTS_DIR = REPO_ROOT / "evaluation" / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description="Wipe the Kivi database.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument("--results", action="store_true", help="Also delete evaluation results.")
    args = parser.parse_args()

    settings = get_settings()
    print(f"This will delete every record in {settings.db_path}")

    if not args.yes:
        try:
            reply = input("Type 'reset' to continue: ").strip().lower()
        except EOFError:
            reply = ""
        if reply != "reset":
            print("Cancelled. Nothing was deleted.")
            return 1

    try:
        path = reset_db()
    except RuntimeError as exc:
        raise SystemExit(str(exc))
    print(f"Database reset: {path}")

    if args.results and RESULTS_DIR.exists():
        removed = 0
        for file in RESULTS_DIR.glob("*"):
            if file.is_file():
                file.unlink()
                removed += 1
        print(f"Removed {removed} file(s) from {RESULTS_DIR}")

    print("\nTo rebuild:  python scripts/seed.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
