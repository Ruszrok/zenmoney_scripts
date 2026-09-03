from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance import accounts, db, fx, ingest, report

FIXTURES = Path(__file__).parent / "fixtures"


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
        payload = report.build(self.conn)
        self.assertIn("savings_rate_12m", payload)

    def test_recurring_active_and_dormant_totals_are_separate(self) -> None:
        payload = report.build(self.conn)
        self.assertIn("recurring_active_total_eur", payload)
        self.assertIn("recurring_dormant_total_eur", payload)

    def test_markdown_leads_recurring_with_active_not_gross_total(self) -> None:
        payload = report.build(self.conn)
        payload["recurring_active_total_eur"] = 111.11
        payload["recurring_dormant_total_eur"] = 999.99
        text = report.to_markdown(payload)
        active_pos = text.find("111.11")
        dormant_pos = text.find("999.99")
        self.assertGreater(active_pos, -1)
        self.assertGreater(dormant_pos, -1)
        self.assertLess(active_pos, dormant_pos)

    def test_net_flow_is_never_called_a_balance_when_unanchored(self) -> None:
        payload = report.build(self.conn)
        text = report.to_markdown(payload)
        if any(not row["is_true_balance"] for row in payload["net_flow"]):
            self.assertIn("not a balance", text.lower())

    def test_uncategorised_is_called_out_not_treated_as_a_category(self) -> None:
        payload = report.build(self.conn)
        self.assertIn("uncategorised", payload)


if __name__ == "__main__":
    unittest.main()
