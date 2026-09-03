"""SQLite connection handling and migrations.

Deliberately built on the standard library `sqlite3` module rather than an ORM:
the schema is small, the queries are explicit, and a reviewer can open
`data/kivi.db` with any SQLite browser and see exactly what the system knows.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from backend.config import REPO_ROOT, get_settings

MIGRATIONS_DIR = REPO_ROOT / "migrations"

_local = threading.local()


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a new connection with sane pragmas and dict-like rows."""
    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_connection() -> sqlite3.Connection:
    """A per-thread connection, created on first use.

    FastAPI serves requests from a thread pool, and SQLite connections are not
    safe to share across threads, so each thread gets its own.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


def close_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def transaction(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """Run a block inside a transaction, rolling back on error."""
    own = conn is None
    connection = conn or get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if own:
            pass  # per-thread connections are long lived


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------
def init_db(db_path: Path | None = None, verbose: bool = False) -> Path:
    """Apply every migration in `migrations/` in filename order (idempotent)."""
    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not files:
            raise RuntimeError(f"No migration files found in {MIGRATIONS_DIR}")
        for sql_file in files:
            conn.executescript(sql_file.read_text(encoding="utf-8"))
            if verbose:
                print(f"  applied {sql_file.name}")
        conn.commit()
    finally:
        conn.close()
    return path


def clear_all_tables(conn: sqlite3.Connection | None = None) -> Path:
    """Empty every table, keeping the file and the schema.

    The in-process equivalent of `reset_db`, and the one the API must use.
    Connections here are thread-local and uvicorn serves requests from a
    threadpool, so when a request handler calls `close_connection()` it closes
    only its own thread's handle - the other workers still hold theirs. On
    Windows those open handles make the file undeletable, so a file-deleting
    reset can never succeed from inside the running server, whatever it does
    first. Truncating in place needs no handle closed and behaves identically
    on every platform.

    `reset_db` is still the right thing for the CLI, where the server is not
    running and removing the file also reclaims its space.
    """
    connection = conn or get_connection()
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    with transaction(connection) as tx:
        # No foreign key gets a chance to complain about deletion order.
        tx.execute("PRAGMA foreign_keys = OFF")
        for table in tables:
            if table == "schema_version":
                continue  # the schema itself is unchanged; keep its version row
            tx.execute(f'DELETE FROM "{table}"')
        # Restart autoincrement so a fresh corpus gets ids from 1, matching what
        # a file-level reset would have produced.
        if any(t == "sqlite_sequence" for t in tables) or True:
            try:
                tx.execute("DELETE FROM sqlite_sequence")
            except sqlite3.OperationalError:
                pass  # the table only exists once an AUTOINCREMENT column is used
        tx.execute("PRAGMA foreign_keys = ON")
    connection.execute("VACUUM")
    return get_settings().db_path


def reset_db(db_path: Path | None = None) -> Path:
    """Delete the database file (and WAL siblings), then re-create the schema."""
    path = db_path or get_settings().db_path
    close_connection()
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(path) + suffix)
        if not candidate.exists():
            continue
        try:
            candidate.unlink()
        except PermissionError as exc:
            # On Windows a running API server holds an open handle to the file,
            # and the raw OSError gives a reviewer nothing to act on.
            raise RuntimeError(
                f"Cannot reset {candidate.name}: another process is using it.\n"
                f"Stop the API server (Ctrl+C in the terminal running uvicorn), "
                f"then run this command again."
            ) from exc
    return init_db(path)


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------
_JSON_COLUMNS = {
    "metadata",
    "entities",
    "tags",
    "detail",
    "retrieved_memory_ids",
    "used_memory_ids",
    "retrieval_detail",
    "metrics",
}


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a row to a plain dict, decoding known JSON columns."""
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key in _JSON_COLUMNS and isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                pass
        elif key == "embedding":
            # Never leak raw vectors into API payloads; expose the size instead.
            continue
        out[key] = value
    return out


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [d for d in (row_to_dict(r) for r in rows) if d is not None]


# ---------------------------------------------------------------------------
# Vector (de)serialisation - float32 packed little-endian
# ---------------------------------------------------------------------------
def pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))
