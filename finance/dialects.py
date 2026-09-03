"""Read both ZenMoney CSV export dialects into one normalised row shape.

ZenMoney emits two incompatible CSV formats and the difference is easy to miss.
Everything the reader knows about them lives in `DIALECTS`; teaching it a third
format should be a one-entry edit.

The trap worth naming: in the full-history export **both account names are
populated on every row**, with a `0` amount on the unused side. A transfer test
based on "are both account names present?" therefore classifies every row as a
transfer and silently empties all spending analysis. `kind` keys on amounts.

**Identity currency vs. stored currency — read this before touching either.**
`RawRow.outcome_currency` / `income_currency` hold the currency this module
believes should be *stored* for the row (used for FX conversion downstream).
That is a different question from what `fingerprint.canonical()` uses for
*identity*, and the two must stay decoupled:

* Storage wants the best real currency it can infer, because a blank
  currency means a transaction that can never be converted to EUR.
* Identity wants exactly today's behaviour, forever — `account_currency()`
  deliberately *drops* the currency for accounts whose name is silent about
  it (`Debts`, `Брокерский счет`, …), because the two CSV dialects disagree
  about what currency to stamp on those accounts. Letting that disagreement
  into the fingerprint would hash the same real transaction two different
  ways depending on which export it came from.

So `account_currency()` is kept, unchanged, **only** for `fingerprint.py` to
call. `_to_row` no longer calls it. Instead `_to_row` calls
`_infer_stored_currency`, which (a) trusts an account's own declared
`(CCY)` prefix using a *broader* regex than `account_currency`'s — one that
also matches `(Czech)(USD) Чехия`-style names — and (b), failing that, for
an equal-amount transfer, borrows the other leg's currency (equal amounts
moving between accounts essentially never happens across a real currency
conversion, so equal ⇒ same currency), and only then falls back to the raw
CSV value. **Do not let `_infer_stored_currency`'s broader regex leak into
`account_currency` or into `fingerprint.canonical()`** — that would change
generated ids for the `(Czech)` accounts, which is the one thing this whole
split exists to avoid.
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
# IDENTITY ONLY — see the module docstring. Do not broaden this; a broader
# match here changes generated transaction ids.
ACCOUNT_CURRENCY = re.compile(r"^\([A-Z]{3}\)")

# STORAGE ONLY. Matches a "(CCY)" token anywhere in the leading run of
# parenthesised groups, so "(Czech)(USD) Чехия" is recognised as declaring
# USD even though its very first token, "(Czech)", is not itself a currency
# code. Still anchored to the prefix — a stray 3-letter uppercase run
# elsewhere in the name (outside the leading parens) never matches.
LEADING_PARENS = re.compile(r"^(?:\([^()]*\))+")
CCY_TOKEN = re.compile(r"\([A-Z]{3}\)")

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

    IDENTITY ONLY (see module docstring) — `fingerprint.canonical()` is the
    only caller that matters here.

    Accounts without a `(CCY)` prefix — `Debts`, `Брокерский счет` — get a
    currency stamped on them arbitrarily, and the two exports disagree: 79
    `Debts` transfers are labelled EUR in the full dump and RUB in the
    per-month dumps. The account name is the only stable signal, so where it
    is silent the currency is dropped from the identity.
    """
    return currency if ACCOUNT_CURRENCY.match(account) else ""


def _declares_currency(account: str) -> bool:
    """STORAGE ONLY — the broadened, `(Czech)(USD)`-aware prefix check."""
    prefix = LEADING_PARENS.match(account)
    return bool(prefix and CCY_TOKEN.search(prefix.group()))


def _infer_stored_currency(
    account: str,
    raw_currency: str,
    other_account: str,
    other_raw_currency: str,
    *,
    is_transfer: bool,
    same_amount: bool,
) -> str:
    """The currency to store for one leg of a row. Never blanked.

    In priority order:

    1. The account's own declared `(CCY)` prefix (via `_declares_currency`,
       not `account_currency` — see module docstring) — trust the raw CSV
       value, since a declared account's currency is not in dispute between
       dialects.
    2. For an equal-amount transfer, the other leg's raw currency. Equal
       amounts moving between two accounts in a genuine cross-currency
       transfer essentially never happens, so equal amounts imply the same
       real currency — which lets a mislabelled leg (`Debts` is stamped EUR
       by the full export no matter what actually moved) borrow the truth
       from its counterpart.
    3. This leg's own raw CSV currency, taken as-is — the fallback for a
       non-transfer row, an unequal-amount transfer, or a transfer whose
       other leg is itself unknown.
    """
    if _declares_currency(account):
        return raw_currency
    if is_transfer and same_amount and other_raw_currency:
        return other_raw_currency
    return raw_currency


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
    outcome_raw_currency = (
        normalise_text(record.get("outcomeCurrencyShortTitle")) if use_outcome else ""
    )
    income_raw_currency = (
        normalise_text(record.get("incomeCurrencyShortTitle")) if use_income else ""
    )
    is_transfer = kind == "transfer"
    same_amount = is_transfer and outcome == income

    return RawRow(
        date=normalise_text(record.get("date")),
        category=normalise_text(record.get("categoryName")),
        payee=normalise_text(record.get("payee")),
        comment=normalise_text(record.get("comment")),
        outcome_account=outcome_account,
        outcome_minor=outcome if use_outcome else 0,
        outcome_currency=(
            _infer_stored_currency(
                outcome_account,
                outcome_raw_currency,
                income_account,
                income_raw_currency,
                is_transfer=is_transfer,
                same_amount=same_amount,
            )
            if use_outcome
            else ""
        ),
        income_account=income_account,
        income_minor=income if use_income else 0,
        income_currency=(
            _infer_stored_currency(
                income_account,
                income_raw_currency,
                outcome_account,
                outcome_raw_currency,
                is_transfer=is_transfer,
                same_amount=same_amount,
            )
            if use_income
            else ""
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
