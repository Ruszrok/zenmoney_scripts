from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from finance import db
from finance.analysis import recurring


class RecurringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        self.conn.execute(
            "INSERT INTO categories (id, full_name, parent, leaf) "
            "VALUES (1,'Отдых и развлечения / Подписки','Отдых и развлечения','Подписки')"
        )
        self.conn.execute(
            "INSERT INTO accounts (id, name, kind) VALUES (1,'(EUR) Bunq','spending')"
        )
        self.counter = 0

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, day: str, eur: float, payee: str = "") -> None:
        self.counter += 1
        minor = int(round(eur * 100))
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, category_id, payee, kind, outcome_account_id,
               outcome_minor, outcome_currency, outcome_eur_minor, fx_source)
            VALUES (?,?,1,?,'outcome',1,?,'EUR',?,'base')
            """,
            (str(self.counter), day, payee, minor, minor),
        )

    def _monthly_series(self, start: str, count: int, eur: float, payee="") -> None:
        first = date.fromisoformat(start)
        for index in range(count):
            self._add((first + timedelta(days=30 * index)).isoformat(), eur, payee)
        self.conn.commit()

    def test_detects_a_monthly_subscription(self) -> None:
        self._monthly_series("2026-01-05", 8, 15.99, "Zoom")
        clusters = recurring.detect(self.conn)
        self.assertEqual(1, len(clusters))
        self.assertEqual(30, clusters[0].period_days)
        self.assertEqual(8, clusters[0].occurrences)

    def test_uses_payee_as_the_label_when_present(self) -> None:
        self._monthly_series("2026-01-05", 8, 15.99, "Zoom")
        self.assertEqual("Zoom", recurring.detect(self.conn)[0].label)

    def test_falls_back_to_category_and_amount_without_a_payee(self) -> None:
        self._monthly_series("2026-01-05", 8, 15.99)
        label = recurring.detect(self.conn)[0].label
        self.assertIn("Подписки", label)
        self.assertIn("15.99", label)

    def test_ignores_irregular_spending(self) -> None:
        for day, eur in (
            ("2026-01-03", 4.20),
            ("2026-01-04", 51.00),
            ("2026-02-19", 9.10),
            ("2026-05-02", 33.00),
        ):
            self._add(day, eur)
        self.conn.commit()
        self.assertEqual([], recurring.detect(self.conn))

    def test_requires_a_minimum_number_of_occurrences(self) -> None:
        self._monthly_series("2026-01-05", 2, 15.99, "Zoom")
        self.assertEqual([], recurring.detect(self.conn))

    def test_amounts_within_tolerance_join_one_cluster(self) -> None:
        first = date.fromisoformat("2026-01-05")
        for index in range(6):
            self._add(
                (first + timedelta(days=30 * index)).isoformat(),
                15.99 if index % 2 else 16.20,
                "Zoom",
            )
        self.conn.commit()
        self.assertEqual(1, len(recurring.detect(self.conn)))

    def test_monthly_load_normalises_an_annual_charge(self) -> None:
        for year in range(2020, 2026):
            self._add(f"{year}-03-05", 120.0, "Domain")
        self.conn.commit()
        cluster = recurring.detect(self.conn)[0]
        self.assertAlmostEqual(10.0, cluster.monthly_eur, places=1)

    def test_dormant_when_the_series_stopped(self) -> None:
        self._monthly_series("2020-01-05", 8, 15.99, "Old")
        clusters = recurring.detect(self.conn, as_of=date(2020, 12, 1))
        self.assertEqual("dormant", clusters[0].status)

    def test_as_of_defaults_to_today(self) -> None:
        # A series that last occurred decades ago is dormant under today's
        # real clock (the default), with no explicit `as_of` passed.
        self._monthly_series("1999-01-05", 8, 15.99, "Old")
        self.assertEqual("dormant", recurring.detect(self.conn)[0].status)


if __name__ == "__main__":
    unittest.main()
