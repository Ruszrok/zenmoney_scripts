"""Resolve every amount to EUR at its own transaction date.

The ECB is the best source but does not cover this dataset: it delisted RUB in
March 2022 and never published KZT. Four layers fill the gap, in descending
order of trust, and each rate records which layer produced it so reports can
disclose how much of a total rests on inference:

  ecb      published reference rates
  implied  derived from the owner's own cross-currency transfers
  manual   `fx_overrides.toml`
  filled   interpolated or carried forward between known points

`rate_for` returns EUR per one unit of the currency — 1 USD is about 0.88 EUR —
which is the inverse of how the ECB quotes it.
"""

from __future__ import annotations

import json
import sqlite3
import tomllib
import urllib.request
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ECB_URL = "https://api.frankfurter.dev/v1/{start}..{end}"
BASE_CURRENCY = "EUR"
OVERRIDES_PATH = Path("fx_overrides.toml")
REQUEST_TIMEOUT = 30.0

# Higher wins when two layers claim the same date and currency.
SOURCE_PRIORITY: dict[str, int] = {"filled": 0, "implied": 1, "manual": 2, "ecb": 3}


def fetch_ecb(
    start: str,
    end: str,
    symbols: list[str],
    opener=urllib.request.urlopen,
) -> dict[str, dict[str, float]]:
    """Fetch ECB rates for a date range, inverted to EUR-per-unit."""
    url = ECB_URL.format(start=start, end=end)
    url += f"?base={BASE_CURRENCY}&symbols={','.join(symbols)}"
    with opener(url, timeout=REQUEST_TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))

    result: dict[str, dict[str, float]] = {}
    for date, quotes in payload.get("rates", {}).items():
        result[date] = {
            currency: 1.0 / value
            for currency, value in quotes.items()
            if value
        }
    return result


def store_rates(
    conn: sqlite3.Connection, rates: dict[str, dict[str, float]], source: str
) -> int:
    """Insert rates, keeping the higher-priority source on conflict."""
    written = 0
    for date, quotes in rates.items():
        for currency, value in quotes.items():
            existing = conn.execute(
                "SELECT source FROM fx_rates WHERE date = ? AND currency = ?",
                (date, currency),
            ).fetchone()
            if existing is not None and SOURCE_PRIORITY.get(
                existing["source"], -1
            ) >= SOURCE_PRIORITY.get(source, -1):
                continue
            conn.execute(
                """
                INSERT INTO fx_rates (date, currency, eur_per_unit, source)
                VALUES (?,?,?,?)
                ON CONFLICT(date, currency)
                DO UPDATE SET eur_per_unit = excluded.eur_per_unit,
                              source = excluded.source
                """,
                (date, currency, value, source),
            )
            written += 1
    conn.commit()
    return written


def rate_for(
    conn: sqlite3.Connection, date: str, currency: str
) -> tuple[float, str] | None:
    """Return `(eur_per_unit, source)` for `currency` on `date`."""
    if currency == BASE_CURRENCY:
        return 1.0, "base"
    row = conn.execute(
        "SELECT eur_per_unit, source FROM fx_rates WHERE date = ? AND currency = ?",
        (date, currency),
    ).fetchone()
    if row is None:
        return None
    return row["eur_per_unit"], row["source"]


def to_eur_minor(amount_minor: int, eur_per_unit: float) -> int:
    """Convert minor units to EUR minor units, rounding half up."""
    converted = Decimal(amount_minor) * Decimal(str(eur_per_unit))
    return int(converted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, dict[str, float]]:
    """Read manual rates. Missing file means no overrides."""
    if not path.exists():
        return {}
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        date: {c: float(v) for c, v in quotes.items()}
        for date, quotes in parsed.get("rates", {}).items()
    }
