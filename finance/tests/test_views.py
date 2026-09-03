from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import accounts, db, fx, ingest

FIXTURES = Path(__file__).parent / "fixtures"


class ViewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        fx.store_rates(self.conn, {"2026-07-03": {"RUB": 0.01}}, "ecb")
        fx.store_rates(
            self.conn, {"2026-07-04": {"RUB": 0.01, "USD": 0.9}}, "ecb"
        )
        fx.materialise(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_spend_excludes_transfers(self) -> None:
        kinds = {
            r["kind"] for r in self.conn.execute("SELECT kind FROM v_spend")
        }
        self.assertEqual({"outcome"}, kinds)

    def test_spend_excludes_korrektirovka(self) -> None:
        labels = {
            r["category"] for r in self.conn.execute("SELECT category FROM v_spend")
        }
        self.assertNotIn("Корректировка", labels)

    def test_spend_includes_ordinary_expenses(self) -> None:
        count = self.conn.execute("SELECT COUNT(*) c FROM v_spend").fetchone()["c"]
        self.assertEqual(2, count, "two coffees only")

    def test_income_flags_passive_interest(self) -> None:
        row = self.conn.execute(
            "SELECT is_passive FROM v_income WHERE category = 'проценты'"
        ).fetchone()
        self.assertEqual(1, row["is_passive"])

    def test_transactions_view_splits_category(self) -> None:
        row = self.conn.execute(
            "SELECT category_parent, category_leaf FROM v_transactions "
            "WHERE date = '2026-07-04'"
        ).fetchone()
        self.assertEqual("Отпуск", row["category_parent"])
        self.assertEqual("2023 France/Switzeland", row["category_leaf"])

    def test_monthly_aggregates_by_month(self) -> None:
        row = self.conn.execute(
            "SELECT month, SUM(spend_eur) s FROM v_monthly GROUP BY month"
        ).fetchone()
        self.assertEqual("2026-07", row["month"])
        self.assertAlmostEqual(8.40, row["s"], places=2)

    def test_deleted_rows_are_absent_from_every_view(self) -> None:
        self.conn.execute("UPDATE transactions SET deleted_at = 'x'")
        for view in ("v_transactions", "v_spend", "v_income", "v_monthly"):
            count = self.conn.execute(f"SELECT COUNT(*) c FROM {view}").fetchone()["c"]
            self.assertEqual(0, count, view)


if __name__ == "__main__":
    unittest.main()
