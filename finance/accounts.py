"""Classify accounts so spending analysis can tell saving from spending.

The savings rate is only meaningful if a move into a deposit account counts as
saving rather than as an expense, and net worth is only meaningful if a credit
card counts as a liability. Neither fact is in the export, so it lives in
`accounts.toml`.

`KIND_HINTS` seeds that file; it is a starting guess, not an authority. The
committed TOML always wins.
"""

from __future__ import annotations

import sqlite3
import tomllib

KINDS = frozenset({"spending", "cash", "savings", "investment", "credit", "debt"})
DEFAULT_KIND = "spending"

# Ordered: the first matching substring wins, so "кредитка" is tested before
# the generic card names that would otherwise claim it.
KIND_HINTS: tuple[tuple[str, str], ...] = (
    ("кредитка", "credit"),
    ("credit card", "credit"),
    ("брокерск", "investment"),
    ("иис", "investment"),
    ("interactive brokers", "investment"),
    ("накопительн", "savings"),
    ("депозит", "savings"),
    ("вклад", "savings"),
    ("наличные", "cash"),
    ("кубышка", "cash"),
    ("cash", "cash"),
    ("debts", "debt"),
    ("долг", "debt"),
)


def guess_kind(name: str) -> str:
    """Best-effort classification of an account from its name."""
    lowered = name.casefold()
    for needle, kind in KIND_HINTS:
        if needle in lowered:
            return kind
    return DEFAULT_KIND


def seed_toml(conn: sqlite3.Connection) -> str:
    """Render every known account as an editable TOML block."""
    rows = conn.execute(
        "SELECT name, currency, kind FROM accounts ORDER BY name"
    ).fetchall()
    lines = [
        "# Account classification for the finance warehouse.",
        "# kind: " + " | ".join(sorted(KINDS)),
        "# alias_of: merge a duplicate account into another by name.",
        "# opening_balance / opening_date: set both to turn net flow into a",
        "# real balance. Without them, reports say 'net flow, not a balance'.",
        "",
    ]
    for row in rows:
        kind = row["kind"] or guess_kind(row["name"])
        lines.append(f'[accounts."{row["name"]}"]')
        lines.append(f'kind = "{kind}"')
        if row["currency"]:
            lines.append(f'# currency = "{row["currency"]}"')
        lines.append("")
    return "\n".join(lines)


def apply_toml(conn: sqlite3.Connection, text: str) -> int:
    """Apply a classification file to the database. Returns rows updated."""
    parsed = tomllib.loads(text)
    entries: dict[str, dict[str, object]] = parsed.get("accounts", {})

    for name, entry in entries.items():
        kind = entry.get("kind", DEFAULT_KIND)
        if kind not in KINDS:
            raise ValueError(f"account {name!r} has unknown kind {kind!r}")

    updated = 0
    for name, entry in entries.items():
        alias_id = None
        alias_name = entry.get("alias_of")
        if alias_name:
            target = conn.execute(
                "SELECT id FROM accounts WHERE name = ?", (alias_name,)
            ).fetchone()
            if target is None:
                raise ValueError(f"alias_of target {alias_name!r} does not exist")
            alias_id = target["id"]
        cursor = conn.execute(
            """
            UPDATE accounts
               SET kind = ?, alias_of = ?,
                   opening_balance_minor = ?, opening_date = ?
             WHERE name = ?
            """,
            (
                entry.get("kind", DEFAULT_KIND),
                alias_id,
                entry.get("opening_balance_minor"),
                entry.get("opening_date"),
                name,
            ),
        )
        updated += cursor.rowcount
    conn.commit()
    return updated
