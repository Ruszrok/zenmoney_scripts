"""Find recurring charges without relying on merchant names.

Payee coverage in this dataset is U-shaped — good in 2013-2019, essentially
absent through 2020-2025, good again in 2026 — so a detector keyed on merchant
name would go blind across the middle of the history. Recurrence is instead
detected on the signature that is always present: category, account, and a
cluster of near-identical amounts. Payee, when it happens to be there, only
supplies a friendlier label.

A cluster is recurring when the median gap between occurrences sits close to a
known period and the gaps are consistent.

Dormancy is measured against `as_of` (a cluster is dormant once it has been
silent for more than `DORMANT_PERIODS` periods as of that date). A cluster is
`new` when it is active and its first occurrence falls within the last
`NEW_WINDOW_DAYS` days of `as_of`. That window is a fixed number of days, not
scaled by the cluster's own period: an early version scaled it
(`period_days * 3`), which kept an annual charge "new" for three years — a
credibility problem for an advisory the user is meant to trust. The
trade-off of a fixed window is deliberate: a genuinely new annual
subscription will not be flagged `new` until its first occurrence is recent
enough in absolute terms, which usually means not until its second
occurrence exists at all. That is preferable to calling a two-year-old line
item "new". `as_of` defaults to today, so by default `detect()` is a
real-time read and its active/dormant/new split will drift as calendar time
passes even against an unchanged database. Callers who need a reproducible
report — one that classifies the same way today and next month — should pass
an explicit `as_of` date instead of relying on the default.

There is deliberately no `price-increased` status. Two clusters sharing a
`(category, account)` signature at different amounts are, on this data,
usually distinct concurrent things (fuel/tolls/parking all under `Машина`;
several overlapping SaaS subscriptions under one business-expense category),
not one service whose price rose over time — telling those apart needs a
merchant name, which is exactly what's missing for 2020-2025. Any succession
heuristic tried against real data flagged ordinary multi-service categories
as "price increases" on every run, so it was removed rather than kept
half-working: being silent about a price rise is better than crying wolf on
one that never happened.
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
NEW_WINDOW_DAYS = 180  # absolute: a period-scaled window keeps an annual
                       # charge "new" for three years
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
    as_of: date | None = None,
) -> list[Cluster]:
    """Recurring clusters, heaviest monthly load first.

    `as_of` is the reference date for dormancy and defaults to today; pass an
    explicit date for a reproducible result.
    """
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

    # "Dormant" measures silence against a real calendar date, not against
    # the dataset's own last row: a wholesale dataset MAX(date) would just
    # equal a cluster's own last occurrence whenever it is the only data
    # present (as in an isolated unit-test fixture), so nothing could ever
    # go silent.
    reference = as_of or date.today()

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
            silent_days = (reference - days[-1]).days
            age_days = (reference - days[0]).days
            if silent_days > period * DORMANT_PERIODS:
                status = "dormant"
            elif age_days <= NEW_WINDOW_DAYS:
                status = "new"
            else:
                status = "active"

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
