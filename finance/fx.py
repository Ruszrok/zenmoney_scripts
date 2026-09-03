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
from datetime import date, timedelta
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
    # The Frankfurter proxy 403s on urllib's default "Python-urllib/x.y"
    # User-Agent; any identifiable UA string satisfies it.
    request = urllib.request.Request(url, headers={"User-Agent": "finance-warehouse/1.0"})
    with opener(request, timeout=REQUEST_TIMEOUT) as response:
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
        day: {c: float(v) for c, v in quotes.items()}
        for day, quotes in parsed.get("rates", {}).items()
    }


def implied_rates(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Derive rates from cross-currency transfers.

    A transfer of 25 000 RUB that arrived as 334.36 USD is an observation of
    the rate actually realised that day. When one side's EUR rate is already
    known, the other side follows.
    """
    rows = conn.execute(
        """
        SELECT date, outcome_minor, outcome_currency, income_minor, income_currency
          FROM transactions
         WHERE kind = 'transfer'
           AND deleted_at IS NULL
           AND outcome_currency <> income_currency
           AND outcome_minor > 0 AND income_minor > 0
        """
    ).fetchall()

    derived: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        out_rate = rate_for(conn, row["date"], row["outcome_currency"])
        in_rate = rate_for(conn, row["date"], row["income_currency"])
        if out_rate is not None and in_rate is None:
            eur = row["outcome_minor"] * out_rate[0]
            value = eur / row["income_minor"]
            currency = row["income_currency"]
        elif in_rate is not None and out_rate is None:
            eur = row["income_minor"] * in_rate[0]
            value = eur / row["outcome_minor"]
            currency = row["outcome_currency"]
        else:
            continue
        derived.setdefault(row["date"], {}).setdefault(currency, []).append(value)

    return {
        day: {c: sum(values) / len(values) for c, values in quotes.items()}
        for day, quotes in derived.items()
    }


def fill_gaps(
    conn: sqlite3.Connection, currencies: list[str], start: str, end: str
) -> int:
    """Interpolate between known rates, carrying the ends outward.

    A single forward walk per currency: `lo_idx` tracks the last anchor at or
    before the current day and only ever advances, so both the day loop and
    the anchor pointer move together in one O(days + anchors) pass — no date
    list is built or rescanned per comparison.

    A day before the first anchor carries the first known rate backward; a
    day after the last anchor carries the last known rate forward; a day
    between two anchors is linearly interpolated between them. A day that
    already has a stored rate is left alone so `store_rates`'s priority
    ordering (a real `ecb`/`implied`/`manual` rate beats `filled`) is never
    at risk of being overwritten here.
    """
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    filled = 0
    for currency in currencies:
        if currency == BASE_CURRENCY:
            continue
        anchors = [
            (date.fromisoformat(row["date"]), row["eur_per_unit"])
            for row in conn.execute(
                "SELECT date, eur_per_unit FROM fx_rates WHERE currency = ? "
                "ORDER BY date",
                (currency,),
            )
        ]
        if not anchors:
            continue
        known = {day for day, _ in anchors}
        count = len(anchors)
        gaps: dict[str, dict[str, float]] = {}
        lo_idx = -1
        day = first
        while day <= last:
            while lo_idx + 1 < count and anchors[lo_idx + 1][0] <= day:
                lo_idx += 1
            if day not in known:
                if lo_idx == -1:
                    value = anchors[0][1]  # before the first anchor
                elif lo_idx == count - 1:
                    value = anchors[lo_idx][1]  # past the last anchor
                else:
                    low, high = anchors[lo_idx], anchors[lo_idx + 1]
                    span = (high[0] - low[0]).days
                    weight = (day - low[0]).days / span if span else 0.0
                    value = low[1] + (high[1] - low[1]) * weight
                gaps.setdefault(day.isoformat(), {})[currency] = value
            day += timedelta(days=1)
        filled += store_rates(conn, gaps, "filled")
    return filled


def materialise(conn: sqlite3.Connection) -> tuple[int, int]:
    """Write EUR amounts onto every transaction. Returns (converted, unresolved)."""
    rows = conn.execute(
        """
        SELECT id, date, outcome_minor, outcome_currency,
               income_minor, income_currency
          FROM transactions
         WHERE deleted_at IS NULL
        """
    ).fetchall()

    converted = unresolved = 0
    for row in rows:
        sources: list[str] = []
        out_eur = in_eur = None
        missing = False

        if row["outcome_minor"]:
            found = rate_for(conn, row["date"], row["outcome_currency"])
            if found is None:
                missing = True
            else:
                out_eur = to_eur_minor(row["outcome_minor"], found[0])
                sources.append(found[1])
        if row["income_minor"]:
            found = rate_for(conn, row["date"], row["income_currency"])
            if found is None:
                missing = True
            else:
                in_eur = to_eur_minor(row["income_minor"], found[0])
                sources.append(found[1])

        if missing:
            unresolved += 1
            continue
        weakest = (
            min(sources, key=lambda s: SOURCE_PRIORITY.get(s, 99)) if sources else None
        )
        conn.execute(
            "UPDATE transactions SET outcome_eur_minor = ?, income_eur_minor = ?, "
            "fx_source = ? WHERE id = ?",
            (out_eur, in_eur, weakest, row["id"]),
        )
        converted += 1
    conn.commit()
    return converted, unresolved


def refresh(
    conn: sqlite3.Connection, opener=urllib.request.urlopen
) -> dict[str, int]:
    """Run every layer in order, then materialise EUR amounts.

    Order matters: ECB first (covers USD across the whole range), then
    implied (which needs an already-known side — ECB or a prior implied
    result — to derive the other), then manual overrides, then filled gaps
    last so it only ever plugs what nothing else resolved.
    """
    span = conn.execute(
        "SELECT MIN(date) AS lo, MAX(date) AS hi FROM transactions "
        "WHERE deleted_at IS NULL"
    ).fetchone()
    if span is None or span["lo"] is None:
        return {"ecb": 0, "implied": 0, "manual": 0, "filled": 0, "unresolved": 0}

    currencies = sorted(
        {
            row["currency"]
            for row in conn.execute(
                "SELECT DISTINCT outcome_currency AS currency FROM transactions "
                "WHERE outcome_currency <> '' UNION "
                "SELECT DISTINCT income_currency FROM transactions "
                "WHERE income_currency <> ''"
            )
            if row["currency"] != BASE_CURRENCY
        }
    )

    counts = {
        "ecb": store_rates(
            conn, fetch_ecb(span["lo"], span["hi"], currencies, opener), "ecb"
        )
    }
    counts["implied"] = store_rates(conn, implied_rates(conn), "implied")
    counts["manual"] = store_rates(conn, load_overrides(), "manual")
    counts["filled"] = fill_gaps(conn, currencies, span["lo"], span["hi"])
    converted, unresolved = materialise(conn)
    counts["converted"] = converted
    counts["unresolved"] = unresolved
    return counts
