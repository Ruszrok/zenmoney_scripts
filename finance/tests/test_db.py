from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from finance import cli, db


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

    def test_migrate_does_not_wipe_existing_data(self) -> None:
        # `migrate()` runs on nearly every subcommand. Same-name-set
        # equality (the test above) would stay green even if `schema.sql`
        # started dropping and recreating a table on every migrate — the
        # table would exist again, just empty. Pin that a second migrate
        # over real data is a genuine no-op, not just a same-shape rebuild.
        conn = db.connect(self.path)
        db.migrate(conn)
        conn.execute(
            "INSERT INTO accounts (id, name, currency, kind) "
            "VALUES (1, 'Test Account', 'EUR', 'spending')"
        )
        conn.commit()

        db.migrate(conn)

        row = conn.execute(
            "SELECT name FROM accounts WHERE id = 1"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("Test Account", row["name"])

    def test_foreign_keys_enforced(self) -> None:
        conn = db.connect(self.path)
        db.migrate(conn)
        self.assertEqual(1, conn.execute("PRAGMA foreign_keys").fetchone()[0])


class CliDbOrderingTest(unittest.TestCase):
    """Pins `--db` accepted after the subcommand, e.g. `finance init --db X`.

    Every documented command (`finance ingest --from ...`, `finance accounts
    --seed`, etc.) uses this ordering. If a later subparser is added without
    `parents=[common]`, argparse rejects `--db` after the subcommand and this
    test fails.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cli.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_db_flag_accepted_after_subcommand(self) -> None:
        exit_code = cli.main(["init", "--db", str(self.path)])
        self.assertEqual(0, exit_code)
        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main()
