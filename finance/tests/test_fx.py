from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from finance import db, fx


def fake_opener(payload: dict) -> object:
    def _open(url: str, timeout: float = 0):  # noqa: ARG001
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    return _open


class FetchEcbTest(unittest.TestCase):
    def test_inverts_ecb_quotes_to_eur_per_unit(self) -> None:
        """ECB quotes EUR->X; the warehouse stores how many EUR one X buys."""
        payload = {
            "amount": 1.0,
            "base": "EUR",
            "rates": {"2021-01-04": {"USD": 1.2296, "RUB": 90.342}},
        }
        rates = fx.fetch_ecb(
            "2021-01-04", "2021-01-04", ["USD", "RUB"], opener=fake_opener(payload)
        )
        self.assertAlmostEqual(1 / 1.2296, rates["2021-01-04"]["USD"], places=9)
        self.assertAlmostEqual(1 / 90.342, rates["2021-01-04"]["RUB"], places=9)

    def test_missing_currency_is_simply_absent(self) -> None:
        payload = {"rates": {"2026-07-01": {"USD": 1.1383}}}
        rates = fx.fetch_ecb(
            "2026-07-01", "2026-07-01", ["USD", "RUB"], opener=fake_opener(payload)
        )
        self.assertNotIn("RUB", rates["2026-07-01"])


class StoreAndReadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_eur_is_always_one_without_being_stored(self) -> None:
        self.assertEqual((1.0, "base"), fx.rate_for(self.conn, "2024-01-01", "EUR"))

    def test_store_then_read_back(self) -> None:
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.9}}, "ecb")
        self.assertEqual((0.9, "ecb"), fx.rate_for(self.conn, "2024-01-02", "USD"))

    def test_unknown_date_returns_none(self) -> None:
        self.assertIsNone(fx.rate_for(self.conn, "2024-01-02", "USD"))

    def test_higher_priority_source_wins_on_conflict(self) -> None:
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.8}}, "filled")
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.9}}, "ecb")
        self.assertEqual((0.9, "ecb"), fx.rate_for(self.conn, "2024-01-02", "USD"))

    def test_lower_priority_source_does_not_overwrite(self) -> None:
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.9}}, "ecb")
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.1}}, "filled")
        self.assertEqual((0.9, "ecb"), fx.rate_for(self.conn, "2024-01-02", "USD"))


class ConversionTest(unittest.TestCase):
    def test_rounds_to_nearest_cent(self) -> None:
        self.assertEqual(90, fx.to_eur_minor(100, 0.895))

    def test_zero_stays_zero(self) -> None:
        self.assertEqual(0, fx.to_eur_minor(0, 0.9))


if __name__ == "__main__":
    unittest.main()
