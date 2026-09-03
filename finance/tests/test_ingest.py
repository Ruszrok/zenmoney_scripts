from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from finance import db, ingest

FIXTURES = Path(__file__).parent / "fixtures"


class IngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "t.db")
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _live(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL"
        ).fetchone()[0]

    def test_first_ingest_inserts_every_row(self) -> None:
        result = ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        self.assertEqual(5, result.rows_seen)
        self.assertEqual(5, result.rows_new)
        self.assertEqual(5, self._live())

    def test_reingest_is_a_no_op(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        again = ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        self.assertEqual(0, again.rows_new)
        self.assertEqual(0, again.rows_updated)
        self.assertEqual(0, again.rows_deleted)
        self.assertEqual(5, self._live())

    def test_other_dialect_adds_nothing(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        cross = ingest.ingest_file(self.conn, FIXTURES / "month_dialect.csv")
        self.assertEqual(0, cross.rows_new)
        self.assertEqual(5, self._live())

    def test_accounts_and_categories_are_registered(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        names = {
            r["name"] for r in self.conn.execute("SELECT name FROM accounts")
        }
        self.assertIn("(EUR) Bunq", names)
        self.assertIn("(USD) Wise", names)
        row = self.conn.execute(
            "SELECT parent, leaf FROM categories WHERE full_name = ?",
            ("Отпуск / 2023 France/Switzeland",),
        ).fetchone()
        self.assertEqual("Отпуск", row["parent"])
        self.assertEqual("2023 France/Switzeland", row["leaf"])

    def _trimmed_missing_middle(self) -> Path:
        """Drop the 2026-07-03 row, keeping 07-02 and 07-05 as range anchors,
        so the removed row falls INSIDE the trimmed file's own date range."""
        lines = (FIXTURES / "full_dialect.csv").read_text("utf-8").splitlines()
        kept = lines[:3] + lines[4:]          # header + rows 1,2 + rows 4,5
        path = self.root / "trimmed.csv"
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return path

    def test_recategorisation_updates_in_place(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        edited = self.root / "edited.csv"
        text = (FIXTURES / "full_dialect.csv").read_text(encoding="utf-8")
        edited.write_text(text.replace("Корректировка", "Личные траты"), "utf-8")
        result = ingest.ingest_file(self.conn, edited)
        self.assertEqual(0, result.rows_new)
        self.assertEqual(1, result.rows_updated)
        self.assertEqual(5, self._live())

    def test_removed_row_is_soft_deleted_within_range(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        result = ingest.ingest_file(self.conn, self._trimmed_missing_middle())
        self.assertEqual(1, result.rows_deleted)
        self.assertEqual(4, self._live())

    def test_reconciliation_does_not_touch_rows_outside_the_range(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        header, *body = (
            (FIXTURES / "full_dialect.csv").read_text("utf-8").strip().splitlines()
        )
        narrow = self.root / "narrow.csv"
        narrow.write_text(f"{header}\n{body[0]}\n", "utf-8")
        result = ingest.ingest_file(self.conn, narrow)
        self.assertEqual(1, result.rows_deleted, "only the 07-02 twin is missing")
        self.assertEqual(4, self._live())

    def test_row_outside_the_files_range_is_never_deleted(self) -> None:
        """A file covering 07-02..07-04 must not touch the 07-05 row. This is
        what stops a single-month import from wiping the rest of history."""
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        lines = (FIXTURES / "full_dialect.csv").read_text("utf-8").splitlines()
        narrow = self.root / "narrow.csv"
        narrow.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        result = ingest.ingest_file(self.conn, narrow)
        self.assertEqual(0, result.rows_deleted)
        self.assertEqual(5, self._live())

    def test_soft_deleted_row_revives_when_it_returns(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        ingest.ingest_file(self.conn, self._trimmed_missing_middle())
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        self.assertEqual(5, self._live())

    def test_batch_is_recorded(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        row = self.conn.execute(
            "SELECT rows_seen, rows_new FROM import_batches"
        ).fetchone()
        self.assertEqual(5, row["rows_seen"])
        self.assertEqual(5, row["rows_new"])


if __name__ == "__main__":
    unittest.main()
