"""Create or update the database schema.

    python scripts/migrate.py

Safe to run repeatedly - every migration is written to be idempotent.
"""

from __future__ import annotations

import sys

import _bootstrap  # noqa: F401

from backend.config import get_settings
from backend.database.db import init_db


def main() -> int:
    settings = get_settings()
    print(f"Applying migrations to {settings.db_path}")
    path = init_db(verbose=True)
    print(f"Schema is up to date: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
