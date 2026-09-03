"""Budgets derived from actual history, and transactions that break the pattern.

An invented target tells you nothing you did not already believe. A budget
taken from what the owner actually spends is falsifiable, so the baseline here
is the trimmed median of the trailing window: discard the extreme months at
both ends, then take the middle. One holiday does not raise the grocery budget
for the rest of the year.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass

DEFAULT_WINDOW_MONTHS = 12
DEFAULT_TRIM = 0.1
DEFAULT_PERCENTILE = 0.95
MIN_HISTORY_MONTHS = 3
MIN_OUTLIER_SAMPLES = 8


@dataclass(frozen=True)
class Baseline:
    category: str
    budget_eur: float
    actual_eur: float
    variance_eur: float
    variance_ratio: float


@dataclass(frozen=True)
class Outlier:
    date: str
    category: str
    payee: str
    amount_eur: float
    threshold_eur: float


def _trimmed_median(values: list[float], trim: float) -> float:
    ordered = sorted(values)
    cut = int(len(ordered) * trim)
    kept = ordered[cut : len(ordered) - cut] or ordered
    return statistics.median(kept)


def baselines(
    conn: sqlite3.Connection,
    month: str,
    window: int = DEFAULT_WINDOW_MONTHS,
    trim: float = DEFAULT_TRIM,
) -> list[Baseline]:
    """Compare `month` against a trimmed median of the preceding `window`."""
    history: dict[str, dict[str, float]] = {}
    for row in conn.execute(
        "SELECT month, category, SUM(spend_eur) AS total FROM v_monthly "
        "WHERE month < ? GROUP BY month, category",
        (month,),
    ):
        history.setdefault(row["category"], {})[row["month"]] = row["total"]

    actuals = {
        row["category"]: row["total"]
        for row in conn.execute(
            "SELECT category, SUM(spend_eur) AS total FROM v_monthly "
            "WHERE month = ? GROUP BY category",
            (month,),
        )
    }

    results: list[Baseline] = []
    for category, by_month in history.items():
        recent = [by_month[m] for m in sorted(by_month)[-window:]]
        if len(recent) < MIN_HISTORY_MONTHS:
            continue
        budget_eur = _trimmed_median(recent, trim)
        actual = actuals.get(category, 0.0)
        variance = actual - budget_eur
        results.append(
            Baseline(
                category=category,
                budget_eur=budget_eur,
                actual_eur=actual,
                variance_eur=variance,
                variance_ratio=(variance / budget_eur) if budget_eur else 0.0,
            )
        )

    results.sort(key=lambda b: b.variance_eur, reverse=True)
    return results


def outliers(
    conn: sqlite3.Connection,
    since: str | None = None,
    percentile: float = DEFAULT_PERCENTILE,
) -> list[Outlier]:
    """Transactions above their own category's percentile threshold."""
    query = (
        "SELECT date, month, COALESCE(category, '(uncategorised)') AS category, "
        "payee, outcome_eur FROM v_spend WHERE outcome_eur IS NOT NULL"
    )
    params: tuple[str, ...] = ()
    if since:
        query += " AND month >= ?"
        params = (since,)

    by_category: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(query, params):
        by_category.setdefault(row["category"], []).append(row)

    found: list[Outlier] = []
    for category, rows in by_category.items():
        if len(rows) < MIN_OUTLIER_SAMPLES:
            continue
        amounts = sorted(r["outcome_eur"] for r in rows)
        index = min(int(len(amounts) * percentile), len(amounts) - 1)
        threshold = amounts[index]
        for row in rows:
            if row["outcome_eur"] > threshold:
                found.append(
                    Outlier(
                        date=row["date"],
                        category=category,
                        payee=row["payee"],
                        amount_eur=row["outcome_eur"],
                        threshold_eur=threshold,
                    )
                )

    found.sort(key=lambda o: o.amount_eur, reverse=True)
    return found
