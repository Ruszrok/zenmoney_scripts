from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import db, ingest, verify

FIXTURES = Path(__file__).parent / "fixtures"


class CoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, date: str) -> None:
        self.conn.execute(
            "INSERT INTO transactions (id, date, kind, outcome_minor, "
            "outcome_currency) VALUES (?,?,'outcome',100,'EUR')",
            (date, date),
        )

    def test_reports_span_and_no_gaps(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        result = verify.coverage(self.conn)
        self.assertEqual("2026-07", result.first_month)
        self.assertEqual("2026-07", result.last_month)
        self.assertEqual([], result.missing)

    def test_names_the_missing_month(self) -> None:
        self._add("2024-01-05")
        self._add("2024-03-05")
        self.conn.commit()
        self.assertEqual(["2024-02"], verify.coverage(self.conn).missing)

    def test_empty_database_reports_no_months(self) -> None:
        result = verify.coverage(self.conn)
        self.assertEqual(0, result.months)


if __name__ == "__main__":
    unittest.main()
