"""Assemble the analyses into one payload and render it as markdown.

`build` produces data and nothing else — no prose, no judgement — so the same
payload feeds the markdown report, the Artifact dashboard, and the advisory
skill's own reading of the numbers. Interpretation belongs to the skill; this
module's only opinions are structural, forced on it by properties of the real
data this warehouse holds:

- Income is lumpy contractor payments, not salary, so a single month's
  "savings rate" swings wildly (a month with no invoice paid can show a rate
  below -1000%). `build` therefore computes a trailing-12-month savings rate
  over *summed* income and expense — never an average of monthly rates, which
  answers a different and misleading question — and the monthly figures are
  kept only as clearly-labelled detail.
- `recurring.detect()` returns every cluster it ever saw, dormant or not; a
  headline "monthly recurring load" that quietly included lapsed charges
  would overstate the real ongoing commitment. Ongoing (`active` + `new`)
  and `dormant` totals are reported apart, bucketed off `ONGOING_STATUSES`
  rather than an inline literal so a future status added to `recurring.py`
  fails loudly here instead of being silently dropped from both totals.
- FX precision is disclosed for the window being reported, not the dataset's
  whole lifetime — a 13-year mix would understate confidence in recent
  numbers that mostly rest on hard (base or ECB) rates.
- Balances do not exist in the source export; every net-flow figure states
  that it is net flow since the first imported month, not a balance, unless
  `accounts.toml` anchors it with an opening balance.
- `(uncategorised)` is a bookkeeping-discipline signal, not a spending
  category, so it always gets its own callout line ahead of the drift and
  budget tables — even though it may *also* still appear ranked inside
  those tables (it earns its rank on the same numbers as everything else
  there); the callout is what keeps it from being mistaken for one more
  line item.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path

from . import verify
from .analysis import budget, cashflow, categories, recurring

REPORT_DIR = Path("reports")
DEFAULT_MONTHS = 24
TOP_N = 12
INFERRED_SOURCES = ("implied", "filled", "unresolved")
UNCATEGORISED = "(uncategorised)"
SPEND_SPIKE_RATIO = 1.6  # a month spending this far above its trailing 12m mean is flagged
# `recurring.Cluster.status` is one of "active", "new", "dormant" (see
# finance/analysis/recurring.py). Everything NOT dormant counts toward the
# ongoing load; naming the statuses here (instead of matching on "not
# dormant") makes a future status addition an explicit decision rather than
# something that silently falls into one bucket or the other.
ONGOING_STATUSES = ("active", "new")
# `accounts.toml`'s 49 classifications (see finance/accounts.py) are
# heuristic guesses seeded from account-name matching, never checked by a
# human, and `v_spend`'s savings/investment exclusion — hence the savings
# rate and every reported spend total — rests entirely on them being right.
# There is currently no review marker anywhere in the schema (no column, no
# audit log), so this is hardcoded rather than computed from real state; it
# exists purely so a payload consumer can detect the caveat programmatically
# instead of only in markdown prose. Flip it only once a real review marker
# is added to the schema and threaded through here.
ACCOUNTS_REVIEWED = False


def _since(conn: sqlite3.Connection, months: int) -> str | None:
    row = conn.execute(
        "SELECT MAX(substr(date, 1, 7)) AS m FROM transactions WHERE deleted_at IS NULL"
    ).fetchone()
    if row is None or row["m"] is None:
        return None
    year, month = int(row["m"][:4]), int(row["m"][5:])
    total = year * 12 + (month - 1) - (months - 1)
    return f"{total // 12}-{total % 12 + 1:02d}"


def _fx_precision(conn: sqlite3.Connection, since_date: str | None) -> dict[str, float]:
    """Share of absolute EUR volume per rate source, optionally windowed.

    Mirrors `verify.fx_precision`'s volume-weighted definition (it is not
    reused directly because that function is always lifetime-scoped); passing
    `since_date` restricts it to transactions on or after that date so a
    report can disclose precision for the window it actually covers.
    """
    query = (
        "SELECT COALESCE(fx_source, 'unresolved') AS source, "
        "SUM(ABS(COALESCE(outcome_eur_minor, 0)) + ABS(COALESCE(income_eur_minor, 0))) "
        "AS volume FROM transactions WHERE deleted_at IS NULL"
    )
    params: tuple[str, ...] = ()
    if since_date:
        query += " AND date >= ?"
        params = (since_date,)
    query += " GROUP BY source"
    rows = conn.execute(query, params).fetchall()
    total = sum(row["volume"] or 0 for row in rows)
    if not total:
        return {}
    return {row["source"]: (row["volume"] or 0) / total for row in rows}


def _as_of(conn: sqlite3.Connection) -> date | None:
    """The most recent live transaction date, as a `date`.

    This is the reference point `recurring.detect()` measures dormancy
    against. Using the wall clock there would make the same database read
    as having fewer and fewer ongoing charges the longer it sits since the
    last import — the headline recurring load must describe the data, not
    the moment the report happens to run.
    """
    row = conn.execute(
        "SELECT MAX(date) AS d FROM transactions WHERE deleted_at IS NULL"
    ).fetchone()
    if row is None or row["d"] is None:
        return None
    return date.fromisoformat(row["d"])


def _kzt_stats(conn: sqlite3.Connection, since_date: str | None) -> dict:
    """KZT-denominated transaction count, EUR volume, and share of total volume.

    `fx_rates` carries one row per calendar day the gap-filler ever ran
    over — 100% by construction, lifetime or windowed, and an artefact of
    the filler rather than a measure of real exposure. This counts actual
    transactions instead, which is what can honestly be called immaterial
    or not.
    """
    query = (
        "SELECT "
        "SUM(CASE WHEN outcome_currency = 'KZT' OR income_currency = 'KZT' "
        "         THEN 1 ELSE 0 END) AS kzt_txns, "
        "SUM(CASE WHEN outcome_currency = 'KZT' "
        "         THEN ABS(COALESCE(outcome_eur_minor, 0)) ELSE 0 END) "
        "+ SUM(CASE WHEN income_currency = 'KZT' "
        "           THEN ABS(COALESCE(income_eur_minor, 0)) ELSE 0 END) AS kzt_minor, "
        "SUM(ABS(COALESCE(outcome_eur_minor, 0)) + ABS(COALESCE(income_eur_minor, 0))) "
        "AS total_minor "
        "FROM transactions WHERE deleted_at IS NULL"
    )
    params: tuple[str, ...] = ()
    if since_date:
        query += " AND date >= ?"
        params = (since_date,)
    row = conn.execute(query, params).fetchone()
    total_minor = row["total_minor"] or 0
    kzt_minor = row["kzt_minor"] or 0
    return {
        "txns": row["kzt_txns"] or 0,
        "eur": kzt_minor / 100.0,
        "share": (kzt_minor / total_minor) if total_minor else 0.0,
    }


def _kzt_note(conn: sqlite3.Connection, since_date: str | None) -> str | None:
    """KZT has no published ECB rate at all; note actual exposure if any."""
    lifetime = _kzt_stats(conn, None)
    if not lifetime["txns"]:
        return None
    window = _kzt_stats(conn, since_date)
    return (
        f"KZT has no published ECB rate — {lifetime['txns']} transaction(s) / "
        f"{lifetime['eur']:,.2f} EUR lifetime, {window['txns']} transaction(s) / "
        f"{window['eur']:,.2f} EUR in this window ({window['share']:.2%} of "
        "window volume), all resolved via fallback layers. Immaterial to "
        "the totals above, but worth knowing about."
    )


def _top_category(conn: sqlite3.Connection, month: str) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT category, SUM(spend_eur) AS total FROM v_monthly "
        "WHERE month = ? GROUP BY category ORDER BY total DESC LIMIT 1",
        (month,),
    ).fetchone()
    if row is None:
        return None
    return row["category"], row["total"]


def _spend_outlier_months(
    conn: sqlite3.Connection,
    rows: list[cashflow.MonthRow],
    trailing_12m: list[float | None],
    since: str,
) -> list[dict]:
    """Months whose spend spikes far above their own trailing 12m mean.

    Real annual events (an autumn tax bill, an August IRS payment) can look
    like bugs in a trend line unless they are named and explained.
    """
    found: list[dict] = []
    for row, mean in zip(rows, trailing_12m):
        if mean is None or mean <= 0 or row.month < since:
            continue
        ratio = row.spend_eur / mean
        if ratio < SPEND_SPIKE_RATIO:
            continue
        top = _top_category(conn, row.month)
        found.append(
            {
                "month": row.month,
                "spend_eur": row.spend_eur,
                "trailing_12m_mean_eur": mean,
                "ratio": ratio,
                "top_category": top[0] if top else None,
                "top_category_eur": top[1] if top else None,
            }
        )
    found.sort(key=lambda r: r["spend_eur"], reverse=True)
    return found


def _empty_payload(coverage: verify.Coverage, months: int) -> dict:
    return {
        "coverage": asdict(coverage),
        "window_months": months,
        "accounts_reviewed": ACCOUNTS_REVIEWED,
        "since": None,
        "fx_precision": {},
        "fx_precision_lifetime": {},
        "fx_notes": [],
        "cashflow": [],
        "cashflow_3m": [],
        "cashflow_12m": [],
        "savings_rate_12m": None,
        "savings_rate_12m_income_eur": 0.0,
        "savings_rate_12m_expense_eur": 0.0,
        "drift": [],
        "uncategorised": {"drift": None, "budget": None},
        "year_over_year": {},
        "recurring": [],
        "recurring_active_total_eur": 0.0,
        "recurring_active_count": 0,
        "recurring_new_total_eur": 0.0,
        "recurring_new_count": 0,
        "recurring_ongoing_total_eur": 0.0,
        "recurring_ongoing_count": 0,
        "recurring_dormant_total_eur": 0.0,
        "recurring_dormant_count": 0,
        "budget": [],
        "outliers": [],
        "spend_outlier_months": [],
        "net_flow": [],
    }


def build(
    conn: sqlite3.Connection, months: int = DEFAULT_MONTHS, as_of: date | None = None
) -> dict:
    """Every number the advisory needs, as plain JSON-safe structures.

    `as_of` is the reference date for recurring-charge dormancy. It defaults
    to the data's own last live transaction date (`_as_of`, above) — never
    the wall clock — so the same database produces the same report no matter
    when it happens to be run. Pass an explicit date only to reproduce a past
    report.
    """
    coverage = verify.coverage(conn)
    since = _since(conn, months)
    if since is None:
        return _empty_payload(coverage, months)

    full_rows = cashflow.monthly(conn)
    full_3m = cashflow.trailing_mean(full_rows, "spend_eur", 3)
    full_12m = cashflow.trailing_mean(full_rows, "spend_eur", 12)
    start = next((i for i, r in enumerate(full_rows) if r.month >= since), len(full_rows))
    window_rows = full_rows[start:]
    window_3m = full_3m[start:]
    window_12m = full_12m[start:]

    last12 = full_rows[-12:]
    income_12m = sum(r.earned_eur + r.passive_eur for r in last12)
    expense_12m = sum(r.spend_eur for r in last12)
    savings_rate_12m = (
        (income_12m - expense_12m) / income_12m if income_12m else None
    )

    since_date = f"{since}-01"

    all_drift = categories.drift(conn)
    all_budget = budget.baselines(conn, coverage.last_month or "")
    all_recurring = recurring.detect(conn, since=since, as_of=as_of or _as_of(conn))
    unknown_statuses = sorted(
        {c.status for c in all_recurring} - set(ONGOING_STATUSES) - {"dormant"}
    )
    if unknown_statuses:
        # A status recurring.py might add later must not silently fall out of
        # both buckets — fail loudly instead so ONGOING_STATUSES gets updated.
        raise ValueError(
            f"recurring.detect() returned unhandled status(es) {unknown_statuses}; "
            "update ONGOING_STATUSES in finance/report.py"
        )
    active = [c for c in all_recurring if c.status == "active"]
    new = [c for c in all_recurring if c.status == "new"]
    ongoing = [c for c in all_recurring if c.status in ONGOING_STATUSES]
    dormant = [c for c in all_recurring if c.status == "dormant"]

    uncategorised_drift = next(
        (d for d in all_drift if d.category == UNCATEGORISED), None
    )
    uncategorised_budget = next(
        (b for b in all_budget if b.category == UNCATEGORISED), None
    )

    fx_notes = []
    note = _kzt_note(conn, since_date)
    if note:
        fx_notes.append(note)

    return {
        "coverage": asdict(coverage),
        "window_months": months,
        "accounts_reviewed": ACCOUNTS_REVIEWED,
        "since": since,
        "fx_precision": _fx_precision(conn, since_date),
        "fx_precision_lifetime": _fx_precision(conn, None),
        "fx_notes": fx_notes,
        "cashflow": [asdict(row) for row in window_rows],
        "cashflow_3m": window_3m,
        "cashflow_12m": window_12m,
        "savings_rate_12m": savings_rate_12m,
        "savings_rate_12m_income_eur": income_12m,
        "savings_rate_12m_expense_eur": expense_12m,
        "drift": [asdict(d) for d in all_drift[:TOP_N]],
        "uncategorised": {
            "drift": asdict(uncategorised_drift) if uncategorised_drift else None,
            "budget": asdict(uncategorised_budget) if uncategorised_budget else None,
        },
        "year_over_year": categories.year_over_year(conn),
        "recurring": [asdict(c) for c in all_recurring[:TOP_N]],
        "recurring_active_total_eur": sum(c.monthly_eur for c in active),
        "recurring_active_count": len(active),
        "recurring_new_total_eur": sum(c.monthly_eur for c in new),
        "recurring_new_count": len(new),
        "recurring_ongoing_total_eur": sum(c.monthly_eur for c in ongoing),
        "recurring_ongoing_count": len(ongoing),
        "recurring_dormant_total_eur": sum(c.monthly_eur for c in dormant),
        "recurring_dormant_count": len(dormant),
        "budget": [asdict(b) for b in all_budget[:TOP_N]],
        "outliers": [asdict(o) for o in budget.outliers(conn, since=since)[:TOP_N]],
        "spend_outlier_months": _spend_outlier_months(
            conn, full_rows, full_12m, since
        ),
        "net_flow": cashflow.net_flow_by_account(conn),
    }


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def to_markdown(payload: dict) -> str:
    """Render the payload. Tables only — the interpretation is the skill's job."""
    coverage = payload["coverage"]
    if not payload["cashflow"] and coverage["months"] == 0:
        return "# Financial advisory\n\nThe warehouse holds no transactions.\n"

    lines = [
        "# Financial advisory",
        "",
        f"Coverage: **{coverage['months']} months**, "
        f"{coverage['first_month']} → **{coverage['last_month']}**. "
        f"Missing: {', '.join(coverage['missing']) or 'none'}.",
        "",
        f"All figures are EUR and stop at **{coverage['last_month']}** — "
        "later months are not in this warehouse yet.",
        "",
    ]

    if not payload.get("accounts_reviewed", True):
        lines += [
            "> **Account classifications are machine-seeded and have not "
            "been reviewed.** Every account's kind in `accounts.toml` was "
            "guessed from its name by a heuristic, not checked by a human. "
            "The savings rate below and every savings/investment exclusion "
            "in the spend totals depend entirely on those guesses being "
            "right. Review `accounts.toml` before trusting those numbers.",
            "",
        ]

    lines += [
        "## Savings rate — trailing 12 months",
        "",
        "Income here is lumpy contractor payments, not a salary, so any single "
        "month's savings rate can swing wildly (a month with no invoice paid "
        "can show a rate below -1000%). The number below sums 12 months of "
        "income and expense first, then takes one ratio — it is **not** an "
        "average of 12 monthly rates.",
        "",
        f"**{_fmt_pct(payload['savings_rate_12m'])}** "
        f"(income {_fmt(payload['savings_rate_12m_income_eur'])} EUR, "
        f"expense {_fmt(payload['savings_rate_12m_expense_eur'])} EUR).",
        "",
    ]

    inferred = sum(
        share
        for source, share in payload["fx_precision"].items()
        if source in INFERRED_SOURCES
    )
    lines += [
        "## Exchange rate precision",
        "",
        f"Precision for the {payload['window_months']}-month window this "
        "report covers (not the dataset's full history, which would "
        "understate confidence in recent numbers):",
        "",
        f"{1 - inferred:.1%} of converted volume in this window uses "
        f"published or base exchange rates; {inferred:.1%} rests on inferred "
        "ones.",
        "",
        "| source | share (window) |",
        "| --- | ---: |",
    ]
    for source, share in sorted(
        payload["fx_precision"].items(), key=lambda kv: -kv[1]
    ):
        lines.append(f"| {source} | {share:.1%} |")
    for note in payload.get("fx_notes", []):
        lines += ["", f"> {note}"]

    lines += [
        "",
        "## Notable spend months",
        "",
    ]
    outlier_months = payload.get("spend_outlier_months", [])
    if outlier_months:
        lines += [
            "These months spent well above their own trailing 12-month "
            "average — often a real annual event (taxes, an IRS payment), "
            "not a change in day-to-day habits:",
            "",
            "| month | spend | trailing 12m avg | driven mostly by |",
            "| --- | ---: | ---: | --- |",
        ]
        for row in outlier_months:
            driver = (
                f"{row['top_category']} ({_fmt(row['top_category_eur'])})"
                if row["top_category"]
                else "—"
            )
            lines.append(
                f"| {row['month']} | {_fmt(row['spend_eur'])} | "
                f"{_fmt(row['trailing_12m_mean_eur'])} | {driver} |"
            )
    else:
        lines.append("None in this window.")

    lines += [
        "",
        "## Monthly cashflow (detail)",
        "",
        "> Monthly savings rate is noisy for lumpy contractor income — treat "
        "it as detail, not the headline. See the trailing-12-month figure "
        "above.",
        "",
        "| month | earned | passive | spend | net | savings rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["cashflow"]:
        lines.append(
            f"| {row['month']} | {_fmt(row['earned_eur'])} | "
            f"{_fmt(row['passive_eur'])} | {_fmt(row['spend_eur'])} | "
            f"{_fmt(row['net_eur'])} | {_fmt_pct(row['savings_rate'])} |"
        )

    lines += [
        "",
        "## Categories drifting upward",
        "",
    ]
    uncategorised_drift = payload["uncategorised"].get("drift")
    if uncategorised_drift:
        lines += [
            f"> **{UNCATEGORISED}** is drifting too "
            f"(+{uncategorised_drift['change_ratio']:.0%}, "
            f"{_fmt(uncategorised_drift['baseline_mean'])} → "
            f"{_fmt(uncategorised_drift['recent_mean'])} EUR/mo). That is a "
            "bookkeeping-discipline signal — transactions not getting "
            "categorised — not a lifestyle change.",
            "",
        ]
    lines += [
        "| category | recent 6m avg | prior 12m avg | change |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in payload["drift"]:
        lines.append(
            f"| {row['category']} | {_fmt(row['recent_mean'])} | "
            f"{_fmt(row['baseline_mean'])} | +{row['change_ratio']:.0%} |"
        )

    ongoing_total = payload["recurring_ongoing_total_eur"]
    ongoing_count = payload["recurring_ongoing_count"]
    new_count = payload["recurring_new_count"]
    dormant_total = payload["recurring_dormant_total_eur"]
    dormant_count = payload["recurring_dormant_count"]
    new_note = f" ({new_count} of them new)" if new_count else ""
    lines += [
        "",
        "## Recurring charges",
        "",
        f"**Ongoing recurring load: {_fmt(ongoing_total)} EUR/mo** across "
        f"{ongoing_count} charge(s){new_note}. A further {_fmt(dormant_total)} "
        f"EUR/mo across {dormant_count} charge(s) has gone dormant (kept "
        "below — worth knowing what lapsed, but it is not part of the "
        "ongoing load).",
        "",
        "| charge | amount | period (days) | monthly load | status |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in payload["recurring"]:
        lines.append(
            f"| {row['label']} | {_fmt(row['amount_eur'])} | {row['period_days']} | "
            f"{_fmt(row['monthly_eur'])} | {row['status']} |"
        )

    lines += [
        "",
        f"## Budget variance — {coverage['last_month']}",
        "",
    ]
    uncategorised_budget = payload["uncategorised"].get("budget")
    if uncategorised_budget:
        lines += [
            f"> **{UNCATEGORISED}** also shows a budget variance of "
            f"{_fmt(uncategorised_budget['variance_eur'])} EUR — again a "
            "bookkeeping gap, not a spending category.",
            "",
        ]
    lines += [
        "| category | budget | actual | variance |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in payload["budget"]:
        lines.append(
            f"| {row['category']} | {_fmt(row['budget_eur'])} | "
            f"{_fmt(row['actual_eur'])} | {_fmt(row['variance_eur'])} |"
        )

    lines += [
        "",
        "## Unusual transactions",
        "",
        "| date | category | payee | amount | category p95 |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in payload["outliers"]:
        lines.append(
            f"| {row['date']} | {row['category']} | {row['payee'] or '—'} | "
            f"{_fmt(row['amount_eur'])} | {_fmt(row['threshold_eur'])} |"
        )

    net_flow_rows = payload["net_flow"]
    anchored = [r for r in net_flow_rows if r["is_true_balance"]]
    unanchored = [r for r in net_flow_rows if not r["is_true_balance"]]
    anchored_verb = "has" if len(anchored) == 1 else "have"

    lines += [
        "",
        "## Net flow by account",
        "",
        "Every figure below is **net flow since the first imported month, "
        "not a balance** unless anchored (see the last column) — "
        f"{len(anchored)} of the {len(net_flow_rows)} accounts below "
        f"{anchored_verb} `opening_balance_minor` set in `accounts.toml`. "
        "Runway is deliberately not computed here; adding "
        "`opening_balance_minor` for an account in `accounts.toml` is what "
        "would turn its net flow into a real balance and enable a runway "
        "figure.",
        "",
        "| account | kind | net flow (EUR) | is a real balance? |",
        "| --- | --- | ---: | --- |",
    ]
    for row in net_flow_rows:
        lines.append(
            f"| {row['account']} | {row['kind'] or '—'} | "
            f"{_fmt(row['net_eur'])} | {'yes' if row['is_true_balance'] else 'no'} |"
        )

    if unanchored:
        lines += [
            "",
            f"> {len(unanchored)} account(s) above have no opening balance in "
            "`accounts.toml`, so their figures are net flow since the first "
            "imported month, **not a balance**.",
        ]

    return "\n".join(lines) + "\n"
