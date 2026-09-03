from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import accounts, db, fx, ingest
from finance.analysis import cashflow

FIXTURES = Path(__file__).parent / "fixtures"


class MonthlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        fx.store_rates(
            self.conn,
            {"2026-07-03": {"RUB": 0.01}, "2026-07-04": {"RUB": 0.01, "USD": 0.9}},
            "ecb",
        )
        fx.materialise(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_spend_sums_only_real_expenses(self) -> None:
        row = cashflow.monthly(self.conn)[0]
        self.assertAlmostEqual(8.40, row.spend_eur, places=2)

    def test_interest_counts_as_passive_not_earned(self) -> None:
        row = cashflow.monthly(self.conn)[0]
        self.assertAlmostEqual(76.71, row.passive_eur, places=2)
        self.assertAlmostEqual(0.0, row.earned_eur, places=2)

    def test_net_is_income_minus_spend(self) -> None:
        row = cashflow.monthly(self.conn)[0]
        self.assertAlmostEqual(
            row.earned_eur + row.passive_eur - row.spend_eur, row.net_eur, places=2
        )

    def test_savings_rate_is_none_without_income(self) -> None:
        self.conn.execute("DELETE FROM transactions WHERE kind = 'income'")
        self.conn.commit()
        self.assertIsNone(cashflow.monthly(self.conn)[0].savings_rate)

    def test_since_filters_earlier_months(self) -> None:
        self.assertEqual([], cashflow.monthly(self.conn, since="2030-01"))


class TrailingMeanTest(unittest.TestCase):
    def _rows(self, values: list[float]) -> list[cashflow.MonthRow]:
        return [
            cashflow.MonthRow(f"2024-{i + 1:02d}", 0.0, 0.0, v, 0.0, None)
            for i, v in enumerate(values)
        ]

    def test_is_none_until_the_window_is_full(self) -> None:
        result = cashflow.trailing_mean(self._rows([1, 2, 3]), "spend_eur", 3)
        self.assertIsNone(result[0])
        self.assertIsNone(result[1])
        self.assertAlmostEqual(2.0, result[2])

    def test_slides_forward(self) -> None:
        result = cashflow.trailing_mean(self._rows([1, 2, 3, 4]), "spend_eur", 3)
        self.assertAlmostEqual(3.0, result[3])


class NetFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        fx.materialise(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_is_not_a_true_balance_without_an_opening_figure(self) -> None:
        rows = cashflow.net_flow_by_account(self.conn)
        bunq = next(r for r in rows if r["account"] == "(EUR) Bunq")
        self.assertFalse(bunq["is_true_balance"])

    def test_becomes_a_true_balance_once_an_opening_figure_exists(self) -> None:
        self.conn.execute(
            "UPDATE accounts SET opening_balance_minor = 1000, "
            "opening_date = '2026-07-01' WHERE name = '(EUR) Bunq'"
        )
        self.conn.commit()
        rows = cashflow.net_flow_by_account(self.conn)
        bunq = next(r for r in rows if r["account"] == "(EUR) Bunq")
        self.assertTrue(bunq["is_true_balance"])


if __name__ == "__main__":
    unittest.main()
