from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import db
from finance.analysis import categories


class CategoryAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        self.conn.execute(
            "INSERT INTO categories (id, full_name, parent, leaf) "
            "VALUES (1,'Еда / Кафе и рестораны','Еда','Кафе и рестораны'),"
            "       (2,'Машина',NULL,'Машина')"
        )
        self.conn.execute("INSERT INTO accounts (id, name, kind) VALUES (1,'B','spending')")
        self._seed()
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, month: str, category_id: int, eur: float) -> None:
        minor = int(round(eur * 100))
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, category_id, kind, outcome_account_id,
               outcome_minor, outcome_currency, outcome_eur_minor, fx_source)
            VALUES (?,?,?,'outcome',1,?,'EUR',?, 'base')
            """,
            (f"{month}-{category_id}-{eur}", f"{month}-15", category_id, minor, minor),
        )

    def _seed(self) -> None:
        # 12 baseline months at 100, then 6 recent months at 200 for category 1.
        months = [f"2025-{m:02d}" for m in range(1, 13)] + [
            f"2026-{m:02d}" for m in range(1, 7)
        ]
        for index, month in enumerate(months):
            self._add(month, 1, 200.0 if index >= 12 else 100.0)
            self._add(month, 2, 50.0)

    def test_matrix_has_a_column_per_month(self) -> None:
        months, data = categories.matrix(self.conn)
        self.assertEqual(18, len(months))
        self.assertAlmostEqual(100.0, data["Еда / Кафе и рестораны"]["2025-01"])
        self.assertAlmostEqual(200.0, data["Еда / Кафе и рестораны"]["2026-06"])

    def test_drift_detects_the_doubled_category(self) -> None:
        results = categories.drift(self.conn)
        top = results[0]
        self.assertEqual("Еда / Кафе и рестораны", top.category)
        self.assertAlmostEqual(200.0, top.recent_mean, places=2)
        self.assertAlmostEqual(100.0, top.baseline_mean, places=2)
        self.assertAlmostEqual(1.0, top.change_ratio, places=6)

    def test_drift_ignores_the_flat_category(self) -> None:
        names = [d.category for d in categories.drift(self.conn)]
        self.assertNotIn("Машина", names)

    def test_drift_ignores_categories_below_the_floor(self) -> None:
        self.assertEqual([], categories.drift(self.conn, min_eur=10_000.0))

    def test_year_over_year_totals(self) -> None:
        totals = categories.year_over_year(self.conn)
        self.assertAlmostEqual(
            1200.0, totals["Еда / Кафе и рестораны"]["2025"]["total_eur"]
        )
        self.assertAlmostEqual(
            1200.0, totals["Еда / Кафе и рестораны"]["2026"]["total_eur"]
        )

    def test_year_over_year_reports_the_month_count_per_year(self) -> None:
        # The fixture has 12 months of 2025 data and only 6 of 2026 — a
        # naive total-vs-total read of the numbers above would understate
        # 2026 by 2x without this count alongside them.
        totals = categories.year_over_year(self.conn)
        self.assertEqual(12, totals["Еда / Кафе и рестораны"]["2025"]["months"])
        self.assertEqual(6, totals["Еда / Кафе и рестораны"]["2026"]["months"])


if __name__ == "__main__":
    unittest.main()
