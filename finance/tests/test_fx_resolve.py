from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance import cli, db, fx, ingest

FIXTURES = Path(__file__).parent / "fixtures"


class ImpliedRatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_derives_unknown_side_from_known_side(self) -> None:
        """25000 RUB -> 334.36 USD, with USD known, pins RUB."""
        fx.store_rates(self.conn, {"2026-07-04": {"USD": 0.9}}, "ecb")
        implied = fx.implied_rates(self.conn)
        self.assertIn("RUB", implied["2026-07-04"])
        expected = (334.36 * 0.9) / 25000
        self.assertAlmostEqual(expected, implied["2026-07-04"]["RUB"], places=9)

    def test_eur_side_needs_no_prior_rate(self) -> None:
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, kind, outcome_minor, outcome_currency,
               income_minor, income_currency)
            VALUES ('x','2026-07-06','transfer',100000,'RUB',100,'EUR')
            """
        )
        implied = fx.implied_rates(self.conn)
        self.assertAlmostEqual(0.001, implied["2026-07-06"]["RUB"], places=9)

    def test_returns_nothing_when_neither_side_is_known(self) -> None:
        implied = fx.implied_rates(self.conn)
        self.assertEqual({}, implied)

    def test_equal_amount_transfer_produces_no_implied_rate(self) -> None:
        """Ruling 16: a genuine cross-currency transfer cannot have equal
        amounts on both sides (that requires an exactly 1.0000 rate), so an
        equal-amount row with differing currency labels is an artifact, not
        an FX observation — e.g. dialects.py's symmetric-swap inference on
        an undeclared/undeclared account pair. It must never seed a rate,
        even when the EUR side trivially resolves via the base-currency
        shortcut and would otherwise imply RUB = 1.0 EUR."""
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, kind, outcome_minor, outcome_currency,
               income_minor, income_currency)
            VALUES ('eq','2013-11-09','transfer',21600,'RUB',21600,'EUR')
            """
        )
        implied = fx.implied_rates(self.conn)
        self.assertNotIn("2013-11-09", implied)

    def test_unequal_amount_cross_currency_transfer_still_derives_a_rate(self) -> None:
        """The exclusion must not be over-broad: a real cross-currency
        transfer (unequal amounts) still derives a rate."""
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, kind, outcome_minor, outcome_currency,
               income_minor, income_currency)
            VALUES ('uneq','2013-11-09','transfer',21600,'RUB',495,'EUR')
            """
        )
        implied = fx.implied_rates(self.conn)
        self.assertIn("RUB", implied["2013-11-09"])
        self.assertAlmostEqual(495 / 21600, implied["2013-11-09"]["RUB"], places=9)


class FillGapsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_interpolates_between_known_points(self) -> None:
        fx.store_rates(
            self.conn, {"2024-01-01": {"USD": 0.90}, "2024-01-03": {"USD": 0.92}}, "ecb"
        )
        fx.fill_gaps(self.conn, ["USD"], "2024-01-01", "2024-01-03")
        value, source = fx.rate_for(self.conn, "2024-01-02", "USD")
        self.assertAlmostEqual(0.91, value, places=6)
        self.assertEqual("filled", source)

    def test_carries_forward_past_the_last_known_point(self) -> None:
        fx.store_rates(self.conn, {"2024-01-01": {"USD": 0.90}}, "ecb")
        fx.fill_gaps(self.conn, ["USD"], "2024-01-01", "2024-01-03")
        self.assertEqual((0.90, "filled"), fx.rate_for(self.conn, "2024-01-03", "USD"))

    def test_carries_backward_before_the_first_known_point(self) -> None:
        fx.store_rates(self.conn, {"2024-01-03": {"USD": 0.92}}, "ecb")
        fx.fill_gaps(self.conn, ["USD"], "2024-01-01", "2024-01-03")
        self.assertEqual((0.92, "filled"), fx.rate_for(self.conn, "2024-01-01", "USD"))

    def test_does_not_overwrite_real_rates(self) -> None:
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.5}}, "ecb")
        fx.fill_gaps(self.conn, ["USD"], "2024-01-01", "2024-01-03")
        self.assertEqual((0.5, "ecb"), fx.rate_for(self.conn, "2024-01-02", "USD"))

    def test_interpolation_weight_is_not_inverted(self) -> None:
        """Asymmetric anchors: a symmetric fixture cannot catch a flipped weight."""
        fx.store_rates(
            self.conn,
            {"2024-01-01": {"USD": 10.0}, "2024-01-11": {"USD": 20.0}},
            "ecb",
        )
        fx.fill_gaps(self.conn, ["USD"], "2024-01-01", "2024-01-11")
        self.assertAlmostEqual(
            13.0, fx.rate_for(self.conn, "2024-01-04", "USD")[0], places=6
        )
        self.assertAlmostEqual(
            17.0, fx.rate_for(self.conn, "2024-01-08", "USD")[0], places=6
        )


class MaterialiseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_eur_rows_convert_without_any_rate_table(self) -> None:
        converted, unresolved = fx.materialise(self.conn)
        row = self.conn.execute(
            "SELECT outcome_eur_minor, fx_source FROM transactions "
            "WHERE date='2026-07-02' LIMIT 1"
        ).fetchone()
        self.assertEqual(420, row["outcome_eur_minor"])
        self.assertEqual("base", row["fx_source"])
        self.assertGreater(converted, 0)

    def test_unresolved_rows_are_counted_and_left_null(self) -> None:
        _, unresolved = fx.materialise(self.conn)
        self.assertGreater(unresolved, 0, "RUB rows have no rate yet")
        row = self.conn.execute(
            "SELECT income_eur_minor FROM transactions WHERE date='2026-07-03'"
        ).fetchone()
        self.assertIsNone(row["income_eur_minor"])

    def test_records_the_weaker_source_for_a_transfer(self) -> None:
        fx.store_rates(self.conn, {"2026-07-04": {"USD": 0.9}}, "ecb")
        fx.store_rates(self.conn, {"2026-07-04": {"RUB": 0.01}}, "filled")
        fx.materialise(self.conn)
        row = self.conn.execute(
            "SELECT fx_source FROM transactions WHERE date='2026-07-04'"
        ).fetchone()
        self.assertEqual("filled", row["fx_source"])


class CliFxGateTest(unittest.TestCase):
    """`fx --refresh` is the only path that may hit the network / mutate."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cli.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_bare_fx_does_not_call_refresh(self) -> None:
        def _boom(*args, **kwargs):
            raise AssertionError("fx.refresh must not run without --refresh")

        with mock.patch.object(fx, "refresh", side_effect=_boom):
            with self.assertRaises(SystemExit):
                cli.main(["fx", "--db", str(self.path)])

    def test_fx_refresh_flag_calls_refresh(self) -> None:
        with mock.patch.object(
            fx, "refresh", return_value={"ecb": 0}
        ) as mocked:
            exit_code = cli.main(["fx", "--refresh", "--db", str(self.path)])
        self.assertEqual(0, exit_code)
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
