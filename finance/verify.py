"""Report what the warehouse actually covers.

Every figure in an advisory has to name its real cutoff rather than implying
the data runs to today, so coverage is checked before anything is interpreted.
Gaps are reported, never silently tolerated.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Coverage:
    first_month: str | None = None
    last_month: str | None = None
    months: int = 0
    missing: list[str] = field(default_factory=list)


def _next_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:])
    return f"{year + 1}-01" if mon == 12 else f"{year}-{mon + 1:02d}"


def coverage(conn: sqlite3.Connection) -> Coverage:
    """Months present between the first and last transaction, and any gaps."""
    present = [
        row["month"]
        for row in conn.execute(
            "SELECT DISTINCT substr(date, 1, 7) AS month FROM transactions "
            "WHERE deleted_at IS NULL ORDER BY month"
        )
    ]
    if not present:
        return Coverage()

    seen = set(present)
    expected: list[str] = []
    cursor = present[0]
    while cursor <= present[-1]:
        expected.append(cursor)
        cursor = _next_month(cursor)

    return Coverage(
        first_month=present[0],
        last_month=present[-1],
        months=len(present),
        missing=[m for m in expected if m not in seen],
    )


def fx_precision(conn: sqlite3.Connection) -> dict[str, float]:
    """Share of absolute EUR volume attributable to each rate source."""
    rows = conn.execute(
        """
        SELECT COALESCE(fx_source, 'unresolved') AS source,
               SUM(ABS(COALESCE(outcome_eur_minor, 0))
                 + ABS(COALESCE(income_eur_minor, 0))) AS volume
          FROM transactions
         WHERE deleted_at IS NULL
         GROUP BY source
        """
    ).fetchall()
    total = sum(row["volume"] or 0 for row in rows)
    if not total:
        return {}
    return {row["source"]: (row["volume"] or 0) / total for row in rows}
