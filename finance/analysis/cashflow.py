"""Monthly income, expense, and savings rate.

Two distinctions carry the whole analysis. Interest (`проценты`) is passive
income and is reported apart from earned income, because a savings rate
inflated by it describes the deposit rather than the behaviour. And balances
are not in the export: a cumulative sum is net flow since the first imported
month, so it is only called a balance when `accounts.toml` supplies an opening
figure to anchor it.

`monthly` and `net_flow_by_account` read different sources on purpose.
`monthly` reads `v_spend`/`v_income`, which already exclude transfers,
`Корректировка`, and savings/investment moves — mixing those into earned
income or true expense would misstate both. `net_flow_by_account` reads the
raw `transactions` table instead: an account's own balance genuinely moves
on a transfer (money left or arrived) and on a correction, so excluding them
there would misstate the one thing this function claims to compute.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

LIQUID_KINDS = ("spending", "cash", "savings")


@dataclass(frozen=True)
class MonthRow:
    month: str
    earned_eur: float
    passive_eur: float
    spend_eur: float
    net_eur: float
    savings_rate: float | None


def monthly(conn: sqlite3.Connection, since: str | None = None) -> list[MonthRow]:
    """One row per month, oldest first."""
    spend = {
        row["month"]: row["total"]
        for row in conn.execute(
            "SELECT month, SUM(COALESCE(outcome_eur, 0)) AS total "
            "FROM v_spend GROUP BY month"
        )
    }
    earned: dict[str, float] = {}
    passive: dict[str, float] = {}
    for row in conn.execute(
        "SELECT month, is_passive, SUM(COALESCE(income_eur, 0)) AS total "
        "FROM v_income GROUP BY month, is_passive"
    ):
        target = passive if row["is_passive"] else earned
        target[row["month"]] = row["total"]

    months = sorted(set(spend) | set(earned) | set(passive))
    if since:
        months = [m for m in months if m >= since]

    result: list[MonthRow] = []
    for month in months:
        e = earned.get(month, 0.0)
        p = passive.get(month, 0.0)
        s = spend.get(month, 0.0)
        income = e + p
        result.append(
            MonthRow(
                month=month,
                earned_eur=e,
                passive_eur=p,
                spend_eur=s,
                net_eur=income - s,
                savings_rate=((income - s) / income) if income else None,
            )
        )
    return result


def trailing_mean(
    rows: list[MonthRow], field: str, window: int
) -> list[float | None]:
    """Trailing mean of `field`, `None` until the window is full."""
    values = [getattr(row, field) for row in rows]
    out: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            out.append(None)
        else:
            chunk = values[index + 1 - window : index + 1]
            out.append(sum(chunk) / window)
    return out


def net_flow_by_account(conn: sqlite3.Connection) -> list[dict]:
    """Per-account EUR net flow, flagged as a real balance only when anchored.

    Without an `opening_balance_minor`/`opening_date` pair in `accounts.toml`,
    this is net flow since the first imported month for that account, not a
    balance — money that was already in the account before then is unknown.
    When both are present, only transactions on or after `opening_date` are
    summed on top of the opening figure: the balance already accounts for
    everything before that date, so including earlier flow too would double
    count it. A balance with no date can't be anchored to a cutoff, so it is
    never reported as a true balance either.
    """
    rows = conn.execute(
        """
        SELECT a.name AS account, a.kind AS kind,
               a.opening_balance_minor AS opening,
               a.opening_date AS opening_date,
               COALESCE((
                 SELECT SUM(COALESCE(t.income_eur_minor, 0))
                   FROM transactions t
                  WHERE t.income_account_id = a.id AND t.deleted_at IS NULL
                    AND (a.opening_date IS NULL OR t.date >= a.opening_date)
               ), 0) -
               COALESCE((
                 SELECT SUM(COALESCE(t.outcome_eur_minor, 0))
                   FROM transactions t
                  WHERE t.outcome_account_id = a.id AND t.deleted_at IS NULL
                    AND (a.opening_date IS NULL OR t.date >= a.opening_date)
               ), 0) AS net_minor
          FROM accounts a
         ORDER BY a.name
        """
    ).fetchall()
    return [
        {
            "account": row["account"],
            "kind": row["kind"],
            "net_eur": (row["net_minor"] + (row["opening"] or 0)) / 100.0,
            "is_true_balance": row["opening"] is not None
            and row["opening_date"] is not None,
        }
        for row in rows
    ]
