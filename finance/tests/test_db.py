from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from finance import db


class MigrateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _tables(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
        return {r["name"] for r in rows}

    def test_migrate_creates_all_tables(self) -> None:
        conn = db.connect(self.path)
        db.migrate(conn)
        names = self._tables(conn)
        for expected in (
            "accounts",
            "categories",
            "transactions",
            "fx_rates",
            "import_batches",
        ):
            self.assertIn(expected, names)

    def test_migrate_is_idempotent(self) -> None:
        conn = db.connect(self.path)
        db.migrate(conn)
        before = self._tables(conn)
        db.migrate(conn)
        self.assertEqual(before, self._tables(conn))

    def test_foreign_keys_enforced(self) -> None:
        conn = db.connect(self.path)
        db.migrate(conn)
        self.assertEqual(1, conn.execute("PRAGMA foreign_keys").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
