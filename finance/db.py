"""Database connection and schema migration.

The entire schema lives in `schema.sql`; `migrate` simply executes it. Every
statement there is `IF NOT EXISTS` or `CREATE OR REPLACE`, so migration is
idempotent and re-running it on a populated database is safe.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/finance.db")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open `path`, creating parent directories, with sane pragmas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply `schema.sql`. Idempotent."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
