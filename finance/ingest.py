"""Load CSV dumps into the warehouse, idempotently.

Re-importing the same data must be a no-op, and re-importing an edited export
must bring the warehouse into line with it. That means three behaviours:

* rows keyed by content fingerprint, so an unchanged row is recognised;
* mutable fields (category, amounts-in-EUR, timestamps) updated in place;
* rows that vanished from a re-export soft-deleted — but only within the date
  range the file actually covers, so importing a single month never wipes the
  rest of the history.

Reconciliation (the "soft-delete anything not seen" step) uses a per-connection
TEMP TABLE of the ids seen in this file rather than an `id NOT IN (?,?,…)`
clause with one bind parameter per row. A 13-year full-history export is
~17k rows, comfortably past SQLite's common 32,766-parameter compile-time
default, and a giant `NOT IN` list is a linear scan either way. The temp
table is cleared at the top of every `ingest_file` call since it persists for
the life of the connection, not the call.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import dialects, fingerprint

CSV_GLOB = "*.csv"
DEFAULT_DUMP_DIR = Path("data/dumps")


@dataclass(frozen=True)
class IngestResult:
    rows_seen: int = 0
    rows_new: int = 0
    rows_updated: int = 0
    rows_deleted: int = 0

    def merge(self, other: IngestResult) -> IngestResult:
        return IngestResult(
            self.rows_seen + other.rows_seen,
            self.rows_new + other.rows_new,
            self.rows_updated + other.rows_updated,
            self.rows_deleted + other.rows_deleted,
        )


def resolve_account(
    conn: sqlite3.Connection, name: str, currency: str
) -> int | None:
    """Return the id for `name`, inserting the account on first sight."""
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM accounts WHERE name = ?", (name,)
    ).fetchone()
    if row is not None:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO accounts (name, currency) VALUES (?, ?)", (name, currency)
    )
    return int(cursor.lastrowid)


def resolve_category(conn: sqlite3.Connection, label: str) -> int | None:
    """Return the id for `label`, inserting the category on first sight."""
    if not label:
        return None
    row = conn.execute(
        "SELECT id FROM categories WHERE full_name = ?", (label,)
    ).fetchone()
    if row is not None:
        return row["id"]
    parent, leaf = dialects.split_category(label)
    cursor = conn.execute(
        "INSERT INTO categories (full_name, parent, leaf) VALUES (?, ?, ?)",
        (label, parent, leaf),
    )
    return int(cursor.lastrowid)


def _upsert(
    conn: sqlite3.Connection,
    row_id: str,
    row: dialects.RawRow,
    source_file: str,
    now: str,
) -> str:
    """Insert or refresh one transaction. Returns 'new' or 'updated' or 'same'."""
    existing = conn.execute(
        "SELECT category_id, deleted_at FROM transactions WHERE id = ?", (row_id,)
    ).fetchone()
    category_id = resolve_category(conn, row.category)

    if existing is None:
        conn.execute(
            """
            INSERT INTO transactions (
              id, date, category_id, payee, comment,
              outcome_account_id, outcome_minor, outcome_currency,
              income_account_id, income_minor, income_currency,
              kind, created_at, changed_at, source_file, imported_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row_id,
                row.date,
                category_id,
                row.payee,
                row.comment,
                resolve_account(conn, row.outcome_account, row.outcome_currency),
                row.outcome_minor,
                row.outcome_currency,
                resolve_account(conn, row.income_account, row.income_currency),
                row.income_minor,
                row.income_currency,
                row.kind,
                row.created_at,
                row.changed_at,
                source_file,
                now,
            ),
        )
        return "new"

    unchanged = (
        existing["category_id"] == category_id and existing["deleted_at"] is None
    )
    conn.execute(
        """
        UPDATE transactions
           SET category_id = ?, changed_at = ?, source_file = ?,
               imported_at = ?, deleted_at = NULL
         WHERE id = ?
        """,
        (category_id, row.changed_at, source_file, now, row_id),
    )
    return "same" if unchanged else "updated"


def ingest_file(conn: sqlite3.Connection, path: Path) -> IngestResult:
    """Load one dump, then reconcile the date range it covers."""
    rows = dialects.read_rows(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source = path.name

    seen_ids: set[str] = set()
    new = updated = 0
    for row_id, row in fingerprint.assign_ids(rows):
        seen_ids.add(row_id)
        outcome = _upsert(conn, row_id, row, source, now)
        if outcome == "new":
            new += 1
        elif outcome == "updated":
            updated += 1

    deleted = 0
    if rows:
        dates = [r.date for r in rows]
        # RULING 2: reconcile via a TEMP TABLE + subquery instead of an
        # `id NOT IN (?,?,…)` clause with one bind parameter per row — a
        # 17k-row full-history file would sit right at (and on older SQLite
        # builds, past) SQLITE_LIMIT_VARIABLE_NUMBER, and is a linear scan
        # regardless. The temp table lives for the connection's lifetime, not
        # this call, so it is cleared first.
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS seen_ids (id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM seen_ids")
        conn.executemany(
            "INSERT OR IGNORE INTO seen_ids (id) VALUES (?)",
            [(i,) for i in seen_ids],
        )
        cursor = conn.execute(
            """
            UPDATE transactions
               SET deleted_at = ?
             WHERE deleted_at IS NULL
               AND date BETWEEN ? AND ?
               AND id NOT IN (SELECT id FROM seen_ids)
            """,
            (now, min(dates), max(dates)),
        )
        deleted = cursor.rowcount

    result = IngestResult(len(rows), new, updated, deleted)
    conn.execute(
        """
        INSERT INTO import_batches
          (ran_at, files, rows_seen, rows_new, rows_updated, rows_deleted)
        VALUES (?,?,?,?,?,?)
        """,
        (
            now,
            json.dumps([source]),
            result.rows_seen,
            result.rows_new,
            result.rows_updated,
            result.rows_deleted,
        ),
    )
    conn.commit()
    return result


def ingest_paths(conn: sqlite3.Connection, paths: list[Path]) -> IngestResult:
    """Ingest files in filename order so later files win on conflict."""
    total = IngestResult()
    for path in sorted(paths):
        total = total.merge(ingest_file(conn, path))
    return total


def discover(folder: Path) -> list[Path]:
    return sorted(folder.glob(CSV_GLOB))
