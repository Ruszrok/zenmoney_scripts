"""Derive stable transaction ids from row content.

ZenMoney exports carry no transaction id, so identity has to come from the
content. Two fields are deliberately excluded:

* `changed_at` shifts on every re-export, so including it would make every
  re-import look like a new row.
* `category` is mutable — recategorising a transaction in ZenMoney must update
  the existing row rather than create a second one.

Genuinely identical transactions on one day (two identical coffees) collide by
construction. They are separated by an occurrence ordinal assigned in a
canonical order that does not depend on how the file happened to be sorted.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from .dialects import RawRow

FIELD_SEPARATOR = "|"


def canonical(row: RawRow) -> str:
    """The identity string for `row`: mutable fields excluded."""
    return FIELD_SEPARATOR.join(
        (
            row.date,
            row.payee,
            row.comment,
            row.outcome_account,
            str(row.outcome_minor),
            row.outcome_currency,
            row.income_account,
            str(row.income_minor),
            row.income_currency,
        )
    )


def _digest(key: str, ordinal: int) -> str:
    return hashlib.sha256(f"{key}#{ordinal}".encode("utf-8")).hexdigest()


def assign_ids(rows: list[RawRow]) -> list[tuple[str, RawRow]]:
    """Pair each row with its id, disambiguating identical rows by ordinal.

    Ordinals come from each row's rank inside its own collision group, sorted
    by `created_at`, so a row keeps its id no matter where it sits in the file.
    Assigning by file position instead would hand the same transaction a
    different id on a re-export that happened to be sorted differently.
    """
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[canonical(row)].append(index)

    ids: list[str] = [""] * len(rows)
    for key, indexes in groups.items():
        # Rows identical even in their timestamps are interchangeable, so the
        # ordinal they each receive is arbitrary — but the set of ids the group
        # produces is the same regardless of input order, which is what ingest
        # depends on.
        ranked = sorted(
            indexes, key=lambda i: (rows[i].created_at, rows[i].changed_at)
        )
        for ordinal, index in enumerate(ranked, start=1):
            ids[index] = _digest(key, ordinal)

    return list(zip(ids, rows))
