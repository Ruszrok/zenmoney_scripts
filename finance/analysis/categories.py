"""Category spending over time, and which categories are quietly growing.

A one-off spike and a sustained shift look identical in a single month's
total. `drift` separates them by comparing a recent window's mean against the
preceding baseline window, so a category that has genuinely moved to a new
level rises to the top while a single large purchase does not.

`min_eur` keeps trivia out of the ranking: a category averaging €3 that doubles
is a 100% rise and entirely uninteresting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

DEFAULT_RECENT_MONTHS = 6
DEFAULT_BASELINE_MONTHS = 12
DEFAULT_MIN_EUR = 20.0


@dataclass(frozen=True)
class Drift:
    category: str
    recent_mean: float
    baseline_mean: float
    change_ratio: float


def matrix(
    conn: sqlite3.Connection, since: str | None = None
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Return `(months, {category: {month: eur}})`, months ascending."""
    query = "SELECT month, category, SUM(spend_eur) AS total FROM v_monthly"
    params: tuple[str, ...] = ()
    if since:
        query += " WHERE month >= ?"
        params = (since,)
    query += " GROUP BY month, category"

    data: dict[str, dict[str, float]] = {}
    months: set[str] = set()
    for row in conn.execute(query, params):
        months.add(row["month"])
        data.setdefault(row["category"], {})[row["month"]] = row["total"]
    return sorted(months), data


def drift(
    conn: sqlite3.Connection,
    recent: int = DEFAULT_RECENT_MONTHS,
    baseline: int = DEFAULT_BASELINE_MONTHS,
    min_eur: float = DEFAULT_MIN_EUR,
) -> list[Drift]:
    """Categories whose recent mean has risen above their baseline mean."""
    months, data = matrix(conn)
    if len(months) < recent + 1:
        return []

    recent_months = months[-recent:]
    baseline_months = months[-(recent + baseline) : -recent]
    if not baseline_months:
        return []

    results: list[Drift] = []
    for category, by_month in data.items():
        recent_mean = sum(by_month.get(m, 0.0) for m in recent_months) / len(
            recent_months
        )
        baseline_mean = sum(by_month.get(m, 0.0) for m in baseline_months) / len(
            baseline_months
        )
        if max(recent_mean, baseline_mean) < min_eur or baseline_mean <= 0:
            continue
        ratio = (recent_mean - baseline_mean) / baseline_mean
        if ratio <= 0:
            continue
        results.append(Drift(category, recent_mean, baseline_mean, ratio))

    results.sort(key=lambda d: d.change_ratio, reverse=True)
    return results


def year_over_year(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Yearly EUR totals per category."""
    result: dict[str, dict[str, float]] = {}
    for row in conn.execute(
        "SELECT substr(month, 1, 4) AS year, category, SUM(spend_eur) AS total "
        "FROM v_monthly GROUP BY year, category"
    ):
        result.setdefault(row["category"], {})[row["year"]] = row["total"]
    return result
