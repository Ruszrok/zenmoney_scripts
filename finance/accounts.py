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


def _toml_string(value: str) -> str:
    """Quote a TOML basic string. Backslash first, so the escaping cannot be forged."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def seed_toml(conn: sqlite3.Connection) -> str:
    """Render every known account as an editable TOML block.

    Round-trips every persisted field (`alias_of`, `opening_balance_minor`,
    `opening_date`) so that `seed` after a hand-edited `apply` does not wipe
    the edit — only fields that are still unset get commented placeholders.
    """
    rows = conn.execute(
        "SELECT id, name, currency, kind, alias_of, "
        "opening_balance_minor, opening_date FROM accounts ORDER BY name"
    ).fetchall()
    id_to_name = {row["id"]: row["name"] for row in rows}
    lines = [
        "# Account classification for the finance warehouse.",
        "# kind: " + " | ".join(sorted(KINDS)),
        "# alias_of: merge a duplicate account into another by name.",
        "# opening_balance_minor / opening_date: set both to turn net flow into a",
        "# real balance. Without them, reports say 'net flow, not a balance'.",
        "",
    ]
    for row in rows:
        kind = row["kind"] or guess_kind(row["name"])
        lines.append(f"[accounts.{_toml_string(row['name'])}]")
        lines.append(f"kind = {_toml_string(kind)}")

        alias_name = id_to_name.get(row["alias_of"]) if row["alias_of"] is not None else None
        if alias_name is not None:
            lines.append(f"alias_of = {_toml_string(alias_name)}")
        else:
            lines.append('# alias_of = "Other Account Name"')

        if row["opening_balance_minor"] is not None:
            lines.append(f"opening_balance_minor = {row['opening_balance_minor']}")
        else:
            lines.append("# opening_balance_minor = 0")

        if row["opening_date"] is not None:
            lines.append(f"opening_date = {_toml_string(row['opening_date'])}")
        else:
            lines.append('# opening_date = "2026-01-01"')

        if row["currency"]:
            lines.append(f'# currency = "{row["currency"]}"')
        lines.append("")
    return "\n".join(lines)


def apply_toml(conn: sqlite3.Connection, text: str) -> int:
    """Apply a classification file to the database. Returns rows updated.

    Validates everything — `kind` values and `alias_of` targets — before
    touching the database, so a bad entry anywhere in the file leaves the
    database completely unmodified rather than partially applied.
    """
    parsed = tomllib.loads(text)
    entries: dict[str, dict[str, object]] = parsed.get("accounts", {})

    alias_ids: dict[str, int | None] = {}
    for name, entry in entries.items():
        kind = entry.get("kind", DEFAULT_KIND)
        if kind not in KINDS:
            raise ValueError(f"account {name!r} has unknown kind {kind!r}")

        alias_name = entry.get("alias_of")
        if alias_name:
            target = conn.execute(
                "SELECT id FROM accounts WHERE name = ?", (alias_name,)
            ).fetchone()
            if target is None:
                raise ValueError(f"alias_of target {alias_name!r} does not exist")
            alias_ids[name] = target["id"]
        else:
            alias_ids[name] = None

    updated = 0
    for name, entry in entries.items():
        cursor = conn.execute(
            """
            UPDATE accounts
               SET kind = ?, alias_of = ?,
                   opening_balance_minor = ?, opening_date = ?
             WHERE name = ?
            """,
            (
                entry.get("kind", DEFAULT_KIND),
                alias_ids[name],
                entry.get("opening_balance_minor"),
                entry.get("opening_date"),
                name,
            ),
        )
        updated += cursor.rowcount
    conn.commit()
    return updated
