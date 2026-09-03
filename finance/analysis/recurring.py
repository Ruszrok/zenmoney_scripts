"""Find recurring charges without relying on merchant names.

Payee coverage in this dataset is U-shaped — good in 2013-2019, essentially
absent through 2020-2025, good again in 2026 — so a detector keyed on merchant
name would go blind across the middle of the history. Recurrence is instead
detected on the signature that is always present: category, account, and a
cluster of near-identical amounts. Payee, when it happens to be there, only
supplies a friendlier label.

A cluster is recurring when the median gap between occurrences sits close to a
known period and the gaps are consistent.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date

PERIODS: dict[str, int] = {
    "weekly": 7,
    "monthly": 30,
    "quarterly": 91,
    "annual": 365,
}
PERIOD_TOLERANCE = 0.25  # median gap may sit this far from the nominal period
GAP_VARIATION_LIMIT = 0.40  # stdev/mean of gaps above this is not a schedule
MIN_OCCURRENCES = 3
DORMANT_PERIODS = 2.0  # silent for this many periods → dormant
DAYS_PER_MONTH = 30.44
AMOUNT_TOLERANCE = 0.05


@dataclass(frozen=True)
class Cluster:
    label: str
    category: str
    account: str
    amount_eur: float
    period_days: int
    occurrences: int
    first_date: str
    last_date: str
    status: str
    monthly_eur: float


def _cluster_amounts(
    rows: list[sqlite3.Row], tolerance: float
) -> list[list[sqlite3.Row]]:
    """Group rows whose amounts sit within `tolerance` of the group's first."""
    groups: list[list[sqlite3.Row]] = []
    for row in sorted(rows, key=lambda r: r["outcome_eur"] or 0.0):
        amount = row["outcome_eur"] or 0.0
        for group in groups:
            anchor = group[0]["outcome_eur"] or 0.0
            if anchor and abs(amount - anchor) / anchor <= tolerance:
                group.append(row)
                break
        else:
            groups.append([row])
    return groups


def _match_period(gaps: list[int]) -> int | None:
    """Return the nominal period the gaps follow, or None if they follow none."""
    if not gaps:
        return None
    median = statistics.median(gaps)
    if len(gaps) > 1:
        spread = statistics.pstdev(gaps)
        if median and spread / median > GAP_VARIATION_LIMIT:
            return None
    for days in PERIODS.values():
        if abs(median - days) / days <= PERIOD_TOLERANCE:
            return days
    return None


def detect(
    conn: sqlite3.Connection,
    since: str | None = None,
    tolerance: float = AMOUNT_TOLERANCE,
) -> list[Cluster]:
    """Recurring clusters, heaviest monthly load first."""
    query = (
        "SELECT date, payee, COALESCE(category, '(uncategorised)') AS category, "
        "COALESCE(outcome_account, '(none)') AS account, outcome_eur "
        "FROM v_spend WHERE outcome_eur IS NOT NULL"
    )
    params: tuple[str, ...] = ()
    if since:
        query += " AND month >= ?"
        params = (since,)

    signatures: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in conn.execute(query, params):
        signatures.setdefault((row["category"], row["account"]), []).append(row)
    if not signatures:
        return []

    # "Dormant" measures silence against the real calendar, not against the
    # dataset's own last row: a wholesale dataset MAX(date) would just equal
    # a cluster's own last occurrence whenever it is the only data present
    # (as in an isolated unit-test fixture), so nothing could ever go silent.
    today = date.today()

    clusters: list[Cluster] = []
    for (category, account), rows in signatures.items():
        for group in _cluster_amounts(rows, tolerance):
            if len(group) < MIN_OCCURRENCES:
                continue
            days = sorted(date.fromisoformat(r["date"]) for r in group)
            gaps = [(b - a).days for a, b in zip(days, days[1:]) if (b - a).days > 0]
            period = _match_period(gaps)
            if period is None:
                continue

            amounts = [r["outcome_eur"] or 0.0 for r in group]
            amount = statistics.median(amounts)
            payees = [r["payee"] for r in group if r["payee"]]
            label = (
                max(set(payees), key=payees.count)
                if payees
                else f"{category} @ {amount:.2f}"
            )
            silent_days = (today - days[-1]).days
            status = "dormant" if silent_days > period * DORMANT_PERIODS else "active"
            if status == "active" and amounts[-1] > amounts[0] * (1 + tolerance):
                status = "price-increased"

            clusters.append(
                Cluster(
                    label=label,
                    category=category,
                    account=account,
                    amount_eur=amount,
                    period_days=period,
                    occurrences=len(group),
                    first_date=days[0].isoformat(),
                    last_date=days[-1].isoformat(),
                    status=status,
                    monthly_eur=amount * DAYS_PER_MONTH / period,
                )
            )

    clusters.sort(key=lambda c: c.monthly_eur, reverse=True)
    return clusters
