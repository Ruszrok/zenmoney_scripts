"""Read both ZenMoney CSV export dialects into one normalised row shape.

ZenMoney emits two incompatible CSV formats and the difference is easy to miss.
Everything the reader knows about them lives in `DIALECTS`; teaching it a third
format should be a one-entry edit.

The trap worth naming: in the full-history export **both account names are
populated on every row**, with a `0` amount on the unused side. A transfer test
based on "are both account names present?" therefore classifies every row as a
transfer and silently empties all spending analysis. `kind` keys on amounts.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import NamedTuple

CATEGORY_SEPARATOR = " / "  # NOT "/" — four labels contain a bare slash
HEADER_PREFIX = "date"  # both dialects start their header row with this
MINOR_UNITS = Decimal("0.01")

# Account names that declare their own currency, e.g. "(EUR) Bunq".
ACCOUNT_CURRENCY = re.compile(r"^\([A-Z]{3}\)")
WHITESPACE = re.compile(r"\s+")


class Dialect(NamedTuple):
    name: str
    delimiter: str


DIALECTS: tuple[Dialect, ...] = (
    Dialect(name="full", delimiter=";"),
    Dialect(name="month", delimiter=","),
)


@dataclass(frozen=True)
class RawRow:
    """One transaction, normalised so both dialects produce identical values."""

    date: str
    category: str
    payee: str
    comment: str
    outcome_account: str
    outcome_minor: int
    outcome_currency: str
    income_account: str
    income_minor: int
    income_currency: str
    kind: str
    created_at: str
    changed_at: str


def parse_amount(text: str) -> int:
    """Return `text` as integer minor units, accepting either decimal style."""
    cleaned = (
        text.strip()
        .replace("\N{NO-BREAK SPACE}", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    if not cleaned:
        return 0
    scaled = (Decimal(cleaned) / MINOR_UNITS).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(scaled)


def split_category(label: str) -> tuple[str | None, str]:
    """Split `"Parent / Leaf"`; a bare slash inside the leaf is not a separator."""
    if CATEGORY_SEPARATOR not in label:
        return None, label
    parent, leaf = label.split(CATEGORY_SEPARATOR, 1)
    return parent, leaf


def normalise_text(value: str | None) -> str:
    """Trim and collapse whitespace runs.

    The two exports disagree about internal spacing — one comment reads
    "Циан.  Занесены" in the per-month dump and "Циан. Занесены" in the full
    one. Without collapsing, the same transaction fingerprints twice.
    """
    return WHITESPACE.sub(" ", (value or "").strip())


def account_currency(account: str, currency: str) -> str:
    """Keep the currency only when the account name declares one.

    Accounts without a `(CCY)` prefix — `Debts`, `Брокерский счет` — get a
    currency stamped on them arbitrarily, and the two exports disagree: 79
    `Debts` transfers are labelled EUR in the full dump and RUB in the
    per-month dumps. The account name is the only stable signal, so where it
    is silent the currency is dropped from the identity.
    """
    return currency if ACCOUNT_CURRENCY.match(account) else ""


def _detect(lines: list[str]) -> tuple[Dialect, int]:
    """Return the dialect and the index of the header line."""
    for index, line in enumerate(lines):
        if not line.startswith(HEADER_PREFIX):
            continue
        for dialect in DIALECTS:
            if line.startswith(f"{HEADER_PREFIX}{dialect.delimiter}"):
                return dialect, index
    raise ValueError("no header row starting with 'date' found")


def _to_row(record: dict[str, str]) -> RawRow:
    outcome = parse_amount(record.get("outcome") or "")
    income = parse_amount(record.get("income") or "")
    if outcome > 0 and income > 0:
        kind = "transfer"
    elif outcome > 0:
        kind = "outcome"
    elif income > 0:
        kind = "income"
    else:
        kind = "outcome"  # zero-value rows keep a valid kind; ingest reports them

    # Collapse the full dialect's phantom side onto the month dialect's shape.
    use_outcome = kind in ("outcome", "transfer")
    use_income = kind in ("income", "transfer")

    outcome_account = (
        normalise_text(record.get("outcomeAccountName")) if use_outcome else ""
    )
    income_account = (
        normalise_text(record.get("incomeAccountName")) if use_income else ""
    )
    return RawRow(
        date=normalise_text(record.get("date")),
        category=normalise_text(record.get("categoryName")),
        payee=normalise_text(record.get("payee")),
        comment=normalise_text(record.get("comment")),
        outcome_account=outcome_account,
        outcome_minor=outcome if use_outcome else 0,
        outcome_currency=account_currency(
            outcome_account,
            normalise_text(record.get("outcomeCurrencyShortTitle"))
            if use_outcome
            else "",
        ),
        income_account=income_account,
        income_minor=income if use_income else 0,
        income_currency=account_currency(
            income_account,
            normalise_text(record.get("incomeCurrencyShortTitle"))
            if use_income
            else "",
        ),
        kind=kind,
        created_at=normalise_text(record.get("createdDate")),
        changed_at=normalise_text(record.get("changedDate")),
    )


def read_rows(path: Path) -> list[RawRow]:
    """Read `path` in whichever dialect it uses, newest-first order preserved."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    dialect, header_index = _detect(lines)
    reader = csv.DictReader(lines[header_index:], delimiter=dialect.delimiter)
    return [_to_row(record) for record in reader]
