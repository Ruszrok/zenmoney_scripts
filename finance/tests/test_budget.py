from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import db
from finance.analysis import budget


class BudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        self.conn.execute(
            "INSERT INTO categories (id, full_name, parent, leaf) "
            "VALUES (1,'Еда / Продукты','Еда','Продукты')"
        )
        self.conn.execute(
            "INSERT INTO categories (id, full_name, parent, leaf) "
            "VALUES (2,'Еда / Кафе и рестораны','Еда','Кафе и рестораны')"
        )
        self.conn.execute(
            "INSERT INTO accounts (id, name, kind) VALUES (1,'B','spending')"
        )
        self.counter = 0

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _add(
        self, day: str, eur: float, payee: str = "", category_id: int = 1
    ) -> None:
        self.counter += 1
        minor = int(round(eur * 100))
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, category_id, payee, kind, outcome_account_id,
               outcome_minor, outcome_currency, outcome_eur_minor, fx_source)
            VALUES (?,?,?,?,'outcome',1,?,'EUR',?,'base')
            """,
            (str(self.counter), day, category_id, payee, minor, minor),
        )

    def test_budget_is_the_trimmed_median_of_history(self) -> None:
        for month in range(1, 13):
            self._add(f"2025-{month:02d}-10", 100.0)
        self._add("2026-01-10", 300.0)
        self.conn.commit()
        result = budget.baselines(self.conn, "2026-01")
        row = next(r for r in result if r.category == "Еда / Продукты")
        self.assertAlmostEqual(100.0, row.budget_eur, places=2)
        self.assertAlmostEqual(300.0, row.actual_eur, places=2)
        self.assertAlmostEqual(200.0, row.variance_eur, places=2)
        self.assertAlmostEqual(2.0, row.variance_ratio, places=6)

    def test_a_single_spike_does_not_move_the_budget(self) -> None:
        for month in range(1, 12):
            self._add(f"2025-{month:02d}-10", 100.0)
        self._add("2025-12-10", 5000.0)
        self._add("2026-01-10", 100.0)
        self.conn.commit()
        row = budget.baselines(self.conn, "2026-01")[0]
        self.assertLess(row.budget_eur, 200.0, "trimming must discard the spike")

    def test_months_without_history_are_skipped(self) -> None:
        self._add("2026-01-10", 100.0)
        self.conn.commit()
        self.assertEqual([], budget.baselines(self.conn, "2026-01"))

    def test_outliers_flag_the_unusual_transaction(self) -> None:
        for day in range(1, 21):
            self._add(f"2026-01-{day:02d}", 10.0)
        self._add("2026-01-25", 500.0, "Big Shop")
        self.conn.commit()
        found = budget.outliers(self.conn)
        self.assertEqual(1, len(found))
        self.assertEqual("Big Shop", found[0].payee)
        self.assertAlmostEqual(500.0, found[0].amount_eur, places=2)

    def test_no_outliers_when_spending_is_uniform(self) -> None:
        for day in range(1, 21):
            self._add(f"2026-01-{day:02d}", 10.0)
        self.conn.commit()
        self.assertEqual([], budget.outliers(self.conn))

    def test_thin_category_spike_is_suppressed_but_a_thick_ones_surfaces(
        self,
    ) -> None:
        # Note on percentile: at the default percentile=0.95, the threshold
        # index `int(N * percentile)` equals `N - 1` (the maximum element
        # itself) for any N <= 20, so a single spike in a small sample can
        # never exceed its own threshold regardless of MIN_OUTLIER_SAMPLES —
        # that guard would be untested at 0.95. percentile=0.5 pushes the
        # threshold index well below the spike even for a 7-row category, so
        # the assertions below actually exercise the gate rather than
        # coasting on percentile math that already protects thin samples.

        # Thin category: 6 ordinary rows plus one spike — 7 total, fewer
        # than MIN_OUTLIER_SAMPLES(8). The spike must NOT be reported.
        for day in range(1, 7):
            self._add(f"2026-01-{day:02d}", 10.0, category_id=2)
        self._add("2026-01-07", 500.0, "Thin Spike", category_id=2)

        # Thick category (default category_id=1): 20 ordinary rows plus one
        # spike — comfortably above the threshold, so its spike must surface.
        for day in range(1, 21):
            self._add(f"2026-01-{day:02d}", 10.0)
        self._add("2026-01-25", 500.0, "Big Shop")
        self.conn.commit()

        found = budget.outliers(self.conn, percentile=0.5)
        payees = {o.payee for o in found}
        self.assertNotIn(
            "Thin Spike",
            payees,
            "MIN_OUTLIER_SAMPLES must suppress outliers from thin categories",
        )
        self.assertIn(
            "Big Shop",
            payees,
            "a category with ample history must still surface its outlier",
        )


if __name__ == "__main__":
    unittest.main()
