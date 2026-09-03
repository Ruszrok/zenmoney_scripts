from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path

from finance import accounts, db, fx, ingest, report
from finance.analysis.recurring import DAYS_PER_MONTH

FIXTURES = Path(__file__).parent / "fixtures"


def _iso(base: date, delta_days: int) -> str:
    return (base + timedelta(days=delta_days)).isoformat()


class ReportTest(unittest.TestCase):
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

    # -- raw-SQL fixture helpers -------------------------------------------
    # These bypass finance.ingest deliberately: the tests below need exact
    # control over which months/categories/statuses land where, which a CSV
    # dialect fixture can't express concisely.

    def _account_id(self, name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM accounts WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO accounts (name, currency, kind) VALUES (?, 'EUR', 'spending')",
            (name,),
        )
        return cur.lastrowid

    def _category_id(self, full_name: str | None) -> int | None:
        if full_name is None:
            return None
        row = self.conn.execute(
            "SELECT id FROM categories WHERE full_name = ?", (full_name,)
        ).fetchone()
        if row:
            return row["id"]
        leaf = full_name.rsplit(" / ", 1)[-1]
        parent = full_name.rsplit(" / ", 1)[0] if " / " in full_name else None
        cur = self.conn.execute(
            "INSERT INTO categories (full_name, parent, leaf) VALUES (?, ?, ?)",
            (full_name, parent, leaf),
        )
        return cur.lastrowid

    def _add_expense(
        self, day: str, amount_eur: float, category: str | None, account: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO transactions (id, date, category_id, outcome_account_id, "
            "outcome_minor, outcome_currency, kind) VALUES (?, ?, ?, ?, ?, 'EUR', 'outcome')",
            (
                str(uuid.uuid4()),
                day,
                self._category_id(category),
                self._account_id(account),
                round(amount_eur * 100),
            ),
        )

    def _add_income(
        self, day: str, amount_eur: float, category: str, account: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO transactions (id, date, category_id, income_account_id, "
            "income_minor, income_currency, kind) VALUES (?, ?, ?, ?, ?, 'EUR', 'income')",
            (
                str(uuid.uuid4()),
                day,
                self._category_id(category),
                self._account_id(account),
                round(amount_eur * 100),
            ),
        )

    def _finish(self) -> None:
        self.conn.commit()
        fx.materialise(self.conn)

    def _add_recurring_series(
        self,
        category: str,
        account: str,
        amount_eur: float,
        offsets: list[int],
        today: date,
    ) -> None:
        """A monthly-spaced spend series, `offsets` days before `today`."""
        for offset in offsets:
            self._add_expense(_iso(today, -offset), amount_eur, category, account)

    # -- tests ----------------------------------------------------------

    def test_payload_is_json_serialisable(self) -> None:
        json.dumps(report.build(self.conn))

    def test_payload_carries_every_section(self) -> None:
        payload = report.build(self.conn)
        for key in (
            "coverage",
            "fx_precision",
            "cashflow",
            "drift",
            "recurring",
            "budget",
            "outliers",
        ):
            self.assertIn(key, payload)

    def test_coverage_cutoff_is_the_real_last_month(self) -> None:
        self.assertEqual("2026-07", report.build(self.conn)["coverage"]["last_month"])

    def test_markdown_states_the_cutoff(self) -> None:
        text = report.to_markdown(report.build(self.conn))
        self.assertIn("2026-07", text)

    def test_markdown_discloses_fx_precision(self) -> None:
        text = report.to_markdown(report.build(self.conn))
        self.assertIn("exchange rate", text.lower())

    def test_empty_database_produces_a_report_not_a_crash(self) -> None:
        self.conn.execute("DELETE FROM transactions")
        self.conn.commit()
        text = report.to_markdown(report.build(self.conn))
        self.assertIn("no transactions", text.lower())

    def test_savings_rate_is_trailing_12_months_not_average_of_monthly(self) -> None:
        # 11 unremarkable months (income 1000, expense 900 -> +10% each),
        # then one month where almost no invoice was paid (income 10,
        # expense 1000 -> -9900%). Sum-then-ratio and average-of-monthly
        # must land in wildly different places on this fixture.
        months = [
            "2026-08",
            "2026-09",
            "2026-10",
            "2026-11",
            "2026-12",
            "2027-01",
            "2027-02",
            "2027-03",
            "2027-04",
            "2027-05",
            "2027-06",
            "2027-07",
        ]
        for month in months[:-1]:
            self._add_income(f"{month}-05", 1000.0, "Зарплата", "Salary Acct")
            self._add_expense(f"{month}-10", 900.0, "Еда", "Spend Acct")
        self._add_income(f"{months[-1]}-05", 10.0, "Зарплата", "Salary Acct")
        self._add_expense(f"{months[-1]}-10", 1000.0, "Еда", "Spend Acct")
        self._finish()

        total_income = 11 * 1000.0 + 10.0
        total_expense = 11 * 900.0 + 1000.0
        sum_then_ratio = (total_income - total_expense) / total_income
        average_of_monthly = (11 * ((1000.0 - 900.0) / 1000.0) + ((10.0 - 1000.0) / 10.0)) / 12

        # The two methods must disagree sharply on this fixture, or the
        # test wouldn't be able to tell them apart.
        self.assertGreater(abs(sum_then_ratio - average_of_monthly), 1.0)

        payload = report.build(self.conn)
        self.assertAlmostEqual(payload["savings_rate_12m"], sum_then_ratio, places=6)
        self.assertNotAlmostEqual(
            payload["savings_rate_12m"], average_of_monthly, places=2
        )

    def test_recurring_active_and_dormant_totals_are_separate(self) -> None:
        today = date.today()
        active_amount, new_amount, dormant_amount = 100.0, 20.0, 15.0

        # Active: started well over NEW_WINDOW_DAYS ago, still firing.
        self._add_recurring_series(
            "Спорт", "Fitness", active_amount,
            [390, 360, 330, 300, 270, 240, 210, 180, 150, 120, 90, 60, 30, 0],
            today,
        )
        # New: first seen recently, still firing.
        self._add_recurring_series(
            "Хобби", "Hobby Acct", new_amount, [60, 30, 0], today
        )
        # Dormant: stopped firing long ago.
        self._add_recurring_series(
            "Развлечения", "Old Sub", dormant_amount, [400, 370, 340, 310], today
        )
        self._finish()

        payload = report.build(self.conn)

        expected_active = active_amount * DAYS_PER_MONTH / 30
        expected_new = new_amount * DAYS_PER_MONTH / 30
        expected_dormant = dormant_amount * DAYS_PER_MONTH / 30

        self.assertAlmostEqual(
            payload["recurring_active_total_eur"], expected_active, places=2
        )
        self.assertEqual(payload["recurring_active_count"], 1)
        self.assertAlmostEqual(
            payload["recurring_new_total_eur"], expected_new, places=2
        )
        self.assertEqual(payload["recurring_new_count"], 1)
        self.assertAlmostEqual(
            payload["recurring_dormant_total_eur"], expected_dormant, places=2
        )
        self.assertEqual(payload["recurring_dormant_count"], 1)
        self.assertAlmostEqual(
            payload["recurring_ongoing_total_eur"],
            expected_active + expected_new,
            places=2,
        )
        self.assertEqual(payload["recurring_ongoing_count"], 2)
        # The dormant total must not have silently absorbed the other two.
        self.assertNotAlmostEqual(
            payload["recurring_dormant_total_eur"],
            payload["recurring_ongoing_total_eur"],
            places=2,
        )

    def test_new_status_cluster_counts_toward_the_ongoing_load(self) -> None:
        """Regression: a `new`-status cluster must not fall out of every bucket.

        recurring.py's Cluster.status is one of "active", "new", "dormant".
        An earlier version of report.py filtered on
        `status in ("active", "price-increased")` for the ongoing total —
        "price-increased" was already dead, and "new" (added later) matched
        neither the ongoing nor the dormant filter, so a brand-new
        subscription silently vanished from both totals.
        """
        today = date.today()
        new_amount = 42.0
        self._add_recurring_series(
            "Подписки", "New Sub Acct", new_amount, [60, 30, 0], today
        )
        self._finish()

        expected_new = new_amount * DAYS_PER_MONTH / 30
        payload = report.build(self.conn)

        matches = [
            c for c in payload["recurring"] if c["category"] == "Подписки"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["status"], "new")

        self.assertAlmostEqual(payload["recurring_new_total_eur"], expected_new, places=2)
        self.assertAlmostEqual(
            payload["recurring_ongoing_total_eur"], expected_new, places=2
        )
        # The cluster must not have been dropped: dormant + ongoing must
        # account for the entire €42/mo cluster, not just part of it.
        self.assertAlmostEqual(
            payload["recurring_ongoing_total_eur"]
            + payload["recurring_dormant_total_eur"],
            expected_new,
            places=2,
        )

    def test_markdown_leads_recurring_with_active_not_gross_total(self) -> None:
        today = date.today()
        self._add_recurring_series("Спорт", "Fitness", 100.0, [200, 170, 140, 110, 80, 50, 20], today)
        self._add_recurring_series("Развлечения", "Old Sub", 15.0, [400, 370, 340, 310], today)
        self._finish()

        payload = report.build(self.conn)
        text = report.to_markdown(payload)

        ongoing_str = f"{payload['recurring_ongoing_total_eur']:,.2f}"
        dormant_str = f"{payload['recurring_dormant_total_eur']:,.2f}"
        ongoing_pos = text.find(ongoing_str)
        dormant_sentence_pos = text.find("gone dormant")

        self.assertGreater(ongoing_pos, -1)
        self.assertGreater(dormant_sentence_pos, -1)
        self.assertLess(ongoing_pos, dormant_sentence_pos)
        self.assertIn(dormant_str, text)

    def test_net_flow_is_never_called_a_balance_when_unanchored(self) -> None:
        payload = report.build(self.conn)
        text = report.to_markdown(payload)
        if any(not row["is_true_balance"] for row in payload["net_flow"]):
            self.assertIn("not a balance", text.lower())

    def test_uncategorised_is_called_out_not_treated_as_a_category(self) -> None:
        # Six months of quiet uncategorised history, then a spike in the
        # month the report treats as "actual" (the warehouse's last month).
        for month in ("2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"):
            self._add_expense(f"{month}-15", 50.0, None, "Misc Acct")
        self._add_expense("2026-07-06", 500.0, None, "Misc Acct")
        self._finish()

        payload = report.build(self.conn)
        uncategorised_budget = payload["uncategorised"]["budget"]
        self.assertIsNotNone(uncategorised_budget)
        self.assertAlmostEqual(uncategorised_budget["budget_eur"], 50.0, places=2)
        self.assertAlmostEqual(uncategorised_budget["actual_eur"], 500.0, places=2)
        self.assertAlmostEqual(uncategorised_budget["variance_eur"], 450.0, places=2)

        text = report.to_markdown(payload)
        self.assertIn("450.00", text)
        self.assertIn("bookkeeping gap", text)


if __name__ == "__main__":
    unittest.main()
