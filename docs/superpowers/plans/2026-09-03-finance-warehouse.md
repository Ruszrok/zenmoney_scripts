# Finance Warehouse & Advisory Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load 13 years of ZenMoney CSV history into a queryable SQLite database and ship four analyses plus a `/finance-advisor` skill that interprets them.

**Architecture:** A stdlib-only Python package `finance/` sniffs two CSV dialects into one normalised row shape, upserts rows keyed by a content fingerprint (so re-imports refresh rather than duplicate), resolves every amount to EUR through a four-layer FX resolver, and exposes SQL views that are the stable contract for all reporting. Analyses read the views; the advisory skill reads the analyses.

**Tech Stack:** Python 3.13 (stdlib only — `sqlite3`, `csv`, `tomllib`, `hashlib`, `urllib`, `unittest`), SQLite 3.53. No pandas, no pytest, no third-party runtime dependencies. Bun/TypeScript in `src/` is untouched.

**Spec:** `docs/superpowers/specs/2026-09-03-finance-warehouse-design.md`

## Global Constraints

- **Stdlib only.** No third-party imports in `finance/`. `pandas` and `pytest` are not installed and must not be added. `matplotlib`/`numpy` exist but must not be used — charts are inline SVG in the Artifact.
- **Python style follows `scripts/detect_zenmoney_log.py`:** `from __future__ import annotations`, `@dataclass`, full type hints, module docstring explaining the one table/structure that drives behaviour.
- **Money is stored as INTEGER minor units** (cents). Never store money as REAL. Views expose `/ 100.0` for convenience.
- **Amounts are always positive**; direction comes from `kind`.
- **`kind` derivation keys on amounts, never on account-name presence** (spec: the full dialect populates both names on all 17,397 rows).
- **Category separator is `" / "` with `maxsplit=1`** — four labels contain a bare `/` inside the child name.
- **Fingerprints exclude `changedDate` and `categoryName`** — both are mutable across re-exports.
- **Test command:** `python3 -m unittest discover -s finance/tests -t .`
- **Database path:** `data/finance.db`, gitignored. Default ingest folder `data/dumps/`.
- Commit after every task. `git config core.hooksPath .githooks` is already active; pre-commit runs Biome on `src/` only and will not touch Python.

## File Structure

| File | Responsibility |
| --- | --- |
| `finance/__init__.py` | Package marker, version constant |
| `finance/__main__.py` | `python3 -m finance` entry → `cli.main()` |
| `finance/schema.sql` | All DDL: tables, indexes, views |
| `finance/db.py` | Connection factory, schema migration, row helpers |
| `finance/dialects.py` | CSV sniffing + normalisation to `RawRow`. The one table driving behaviour is `DIALECTS`. |
| `finance/fingerprint.py` | Canonical string + sha256 id + occurrence ordinals |
| `finance/ingest.py` | Folder scan, upsert, range-scoped reconciliation |
| `finance/accounts.py` | `accounts.toml` seeding, `alias_of` merging, kind application |
| `finance/fx.py` | Four-layer rate resolver + EUR materialisation |
| `finance/verify.py` | Coverage report, gap detection, FX precision audit |
| `finance/analysis/cashflow.py` | Income/expense/savings rate/net flow |
| `finance/analysis/categories.py` | Month×category matrix, moving averages, drift |
| `finance/analysis/recurring.py` | (category, account, amount) recurrence clusters |
| `finance/analysis/budget.py` | Trimmed-history budgets, variance, outliers |
| `finance/report.py` | Composes analyses into advisory markdown + chart data JSON |
| `finance/cli.py` | Argument parsing, subcommand dispatch |
| `finance/tests/fixtures/full_dialect.csv` | Semicolon fixture |
| `finance/tests/fixtures/month_dialect.csv` | Comma fixture, same transactions |
| `finance/tests/test_*.py` | One test module per source module |
| `accounts.toml` | Account → kind, alias_of, opening_balance. Committed. |
| `fx_overrides.toml` | Manual FX rates. Committed. |
| `.claude/skills/finance-advisor/SKILL.md` | The advisory skill |

---

### Task 1: Package skeleton, schema, and database bootstrap

**Files:**
- Create: `finance/__init__.py`, `finance/__main__.py`, `finance/db.py`, `finance/schema.sql`, `finance/tests/__init__.py`, `finance/cli.py`
- Test: `finance/tests/test_db.py`
- Modify: `package.json` (add scripts), `.gitignore` (add `data/dumps/`)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `finance.db.connect(path: Path) -> sqlite3.Connection` — foreign keys on, `Row` factory
  - `finance.db.migrate(conn: sqlite3.Connection) -> None` — idempotent, executes `schema.sql`
  - `finance.db.DEFAULT_DB_PATH: Path` = `Path("data/finance.db")`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_db.py`:

```python
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from finance import db


class MigrateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _tables(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
        return {r["name"] for r in rows}

    def test_migrate_creates_all_tables(self) -> None:
        conn = db.connect(self.path)
        db.migrate(conn)
        names = self._tables(conn)
        for expected in (
            "accounts",
            "categories",
            "transactions",
            "fx_rates",
            "import_batches",
        ):
            self.assertIn(expected, names)

    def test_migrate_is_idempotent(self) -> None:
        conn = db.connect(self.path)
        db.migrate(conn)
        before = self._tables(conn)
        db.migrate(conn)
        self.assertEqual(before, self._tables(conn))

    def test_foreign_keys_enforced(self) -> None:
        conn = db.connect(self.path)
        db.migrate(conn)
        self.assertEqual(1, conn.execute("PRAGMA foreign_keys").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_db -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finance'`

- [ ] **Step 3: Write the schema**

Create `finance/schema.sql` (views are added in Task 8; tables only for now):

```sql
CREATE TABLE IF NOT EXISTS accounts (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  currency   TEXT,
  kind       TEXT,
  alias_of   INTEGER REFERENCES accounts(id),
  opening_balance_minor INTEGER,
  opening_date TEXT
);

CREATE TABLE IF NOT EXISTS categories (
  id        INTEGER PRIMARY KEY,
  full_name TEXT NOT NULL UNIQUE,
  parent    TEXT,
  leaf      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
  id                 TEXT PRIMARY KEY,
  date               TEXT NOT NULL,
  category_id        INTEGER REFERENCES categories(id),
  payee              TEXT NOT NULL DEFAULT '',
  comment            TEXT NOT NULL DEFAULT '',
  outcome_account_id INTEGER REFERENCES accounts(id),
  outcome_minor      INTEGER NOT NULL DEFAULT 0,
  outcome_currency   TEXT NOT NULL DEFAULT '',
  income_account_id  INTEGER REFERENCES accounts(id),
  income_minor       INTEGER NOT NULL DEFAULT 0,
  income_currency    TEXT NOT NULL DEFAULT '',
  kind               TEXT NOT NULL CHECK (kind IN ('outcome','income','transfer')),
  outcome_eur_minor  INTEGER,
  income_eur_minor   INTEGER,
  fx_source          TEXT,
  created_at         TEXT,
  changed_at         TEXT,
  source_file        TEXT,
  imported_at        TEXT,
  deleted_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_date    ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_kind    ON transactions(kind);
CREATE INDEX IF NOT EXISTS idx_tx_cat     ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_tx_deleted ON transactions(deleted_at);

CREATE TABLE IF NOT EXISTS fx_rates (
  date         TEXT NOT NULL,
  currency     TEXT NOT NULL,
  eur_per_unit REAL NOT NULL,
  source       TEXT NOT NULL CHECK (source IN ('ecb','implied','manual','filled')),
  PRIMARY KEY (date, currency)
);

CREATE TABLE IF NOT EXISTS import_batches (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at        TEXT NOT NULL,
  files         TEXT NOT NULL,
  rows_seen     INTEGER NOT NULL DEFAULT 0,
  rows_new      INTEGER NOT NULL DEFAULT 0,
  rows_updated  INTEGER NOT NULL DEFAULT 0,
  rows_deleted  INTEGER NOT NULL DEFAULT 0
);
```

- [ ] **Step 4: Write the minimal implementation**

Create `finance/__init__.py`:

```python
"""SQLite warehouse and analytics for ZenMoney CSV exports."""

from __future__ import annotations

__version__ = "0.1.0"
```

Create `finance/db.py`:

```python
"""Database connection and schema migration.

The entire schema lives in `schema.sql`; `migrate` simply executes it. Every
statement there is `IF NOT EXISTS` or `CREATE OR REPLACE`, so migration is
idempotent and re-running it on a populated database is safe.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/finance.db")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open `path`, creating parent directories, with sane pragmas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply `schema.sql`. Idempotent."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
```

Create `finance/tests/__init__.py` as an empty file.

Create `finance/cli.py`:

```python
"""Command-line entry point for the finance warehouse."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finance")
    parser.add_argument(
        "--db", type=Path, default=db.DEFAULT_DB_PATH, help="database path"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the database and apply the schema")

    args = parser.parse_args(argv)
    if args.command == "init":
        conn = db.connect(args.db)
        db.migrate(conn)
        print(f"initialised {args.db}")
        return 0
    return 1
```

Create `finance/__main__.py`:

```python
from __future__ import annotations

import sys

from .cli import main

sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover -s finance/tests -t . -v`
Expected: PASS, 3 tests

- [ ] **Step 6: Wire up scripts and gitignore**

In `package.json`, add to `"scripts"`:

```json
"finance": "python3 -m finance",
"test:py": "python3 -m unittest discover -s finance/tests -t .",
```

and change `"check"` to:

```json
"check": "bun run typecheck && bun run test && bun run test:py"
```

Append to `.gitignore`:

```
data/dumps/
```

Then add the Python tests to `.githooks/pre-push`, before the final `echo`:

```bash
echo "[pre-push] python tests…"
python3 -m unittest discover -s finance/tests -t .
```

- [ ] **Step 7: Verify end to end**

Run: `python3 -m finance init --db /tmp/t.db && python3 -m finance init --db /tmp/t.db && rm -f /tmp/t.db*`
Expected: prints `initialised /tmp/t.db` twice with no error

- [ ] **Step 8: Commit**

```bash
git add finance package.json .gitignore .githooks/pre-push
git commit -m "feat(finance): package skeleton, SQLite schema, and migration"
```

---

### Task 2: Two-dialect CSV reader

This is the highest-risk task in the plan. Getting `kind` wrong here silently empties every downstream analysis.

**Files:**
- Create: `finance/dialects.py`, `finance/tests/fixtures/full_dialect.csv`, `finance/tests/fixtures/month_dialect.csv`
- Test: `finance/tests/test_dialects.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `finance.dialects.RawRow` — frozen dataclass with fields `date, category, payee, comment, outcome_account, outcome_minor, outcome_currency, income_account, income_minor, income_currency, kind, created_at, changed_at` (all `str` except `outcome_minor`/`income_minor` which are `int`)
  - `finance.dialects.read_rows(path: Path) -> list[RawRow]`
  - `finance.dialects.split_category(label: str) -> tuple[str | None, str]` returning `(parent, leaf)`
  - `finance.dialects.parse_amount(text: str) -> int` returning minor units

- [ ] **Step 1: Create the fixtures**

Create `finance/tests/fixtures/full_dialect.csv` (semicolon, dot decimals, no preamble, trailing `qrCode` column). Note rows 1–2 are deliberately identical apart from `createdDate`:

```
date;categoryName;payee;comment;outcomeAccountName;outcome;outcomeCurrencyShortTitle;incomeAccountName;income;incomeCurrencyShortTitle;createdDate;changedDate;qrCode
2026-07-02;"Еда / Кафе и рестораны";"Acento Coffee";;"(EUR) Bunq";"4.20";EUR;"(EUR) Bunq";"0";EUR;"2026-08-02 17:52:24";"2026-08-02 14:52:25";
2026-07-02;"Еда / Кафе и рестораны";"Acento Coffee";;"(EUR) Bunq";"4.20";EUR;"(EUR) Bunq";"0";EUR;"2026-08-02 17:53:00";"2026-08-02 14:53:00";
2026-07-03;"проценты";;;"(RUB) Тинькофф депозит 8";"0";RUB;"(RUB) Тинькофф депозит 8";"7670.96";RUB;"2026-08-02 17:32:41";"2026-08-02 14:32:41";
2026-07-04;"Отпуск / 2023 France/Switzeland";;;"(RUB) Тинькофф Карта";"25000";RUB;"(USD) Wise";"334.36";USD;"2026-08-02 17:00:00";"2026-08-02 14:00:00";
2026-07-05;"Корректировка";;;"(EUR) Bunq";"10";EUR;"(EUR) Bunq";"0";EUR;"2026-08-02 17:10:00";"2026-08-02 14:10:00";
```

Create `finance/tests/fixtures/month_dialect.csv` — **the same five transactions**, in the comma dialect with a BOM, a metadata line, two blank lines, comma decimals, a space thousands separator, and empty unused sides. Write this file with a UTF-8 BOM:

```
zm_dump_2011,1788437723,,,"4,0",


date,categoryName,payee,comment,outcomeAccountName,outcome,outcomeCurrencyShortTitle,incomeAccountName,income,incomeCurrencyShortTitle,createdDate,changedDate
2026-07-02,"Еда / Кафе и рестораны","Acento Coffee",,"(EUR) Bunq","4,20",EUR,,,,"2026-08-02 17:52:24","2026-08-02 14:52:25"
2026-07-02,"Еда / Кафе и рестораны","Acento Coffee",,"(EUR) Bunq","4,20",EUR,,,,"2026-08-02 17:53:00","2026-08-02 14:53:00"
2026-07-03,"проценты",,,,,,"(RUB) Тинькофф депозит 8","7670,96",RUB,"2026-08-02 17:32:41","2026-08-02 14:32:41"
2026-07-04,"Отпуск / 2023 France/Switzeland",,,"(RUB) Тинькофф Карта","25 000,00",RUB,"(USD) Wise","334,36",USD,"2026-08-02 17:00:00","2026-08-02 14:00:00"
2026-07-05,"Корректировка",,,"(EUR) Bunq","10,00",EUR,,,,"2026-08-02 17:10:00","2026-08-02 14:10:00"
```

Create the BOM with:

```bash
python3 -c "
from pathlib import Path
p = Path('finance/tests/fixtures/month_dialect.csv')
p.write_bytes(b'\xef\xbb\xbf' + p.read_bytes())
"
```

- [ ] **Step 2: Write the failing test**

Create `finance/tests/test_dialects.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path

from finance import dialects

FIXTURES = Path(__file__).parent / "fixtures"


class ParseAmountTest(unittest.TestCase):
    def test_dot_decimal(self) -> None:
        self.assertEqual(420, dialects.parse_amount("4.20"))

    def test_comma_decimal(self) -> None:
        self.assertEqual(420, dialects.parse_amount("4,20"))

    def test_space_thousands_separator(self) -> None:
        self.assertEqual(2500000, dialects.parse_amount("25 000,00"))

    def test_integer_without_decimals(self) -> None:
        self.assertEqual(2500000, dialects.parse_amount("25000"))

    def test_empty_is_zero(self) -> None:
        self.assertEqual(0, dialects.parse_amount(""))

    def test_rounds_half_up(self) -> None:
        self.assertEqual(767096, dialects.parse_amount("7670.96"))


class SplitCategoryTest(unittest.TestCase):
    def test_two_level(self) -> None:
        self.assertEqual(
            ("Еда", "Кафе и рестораны"),
            dialects.split_category("Еда / Кафе и рестораны"),
        )

    def test_top_level(self) -> None:
        self.assertEqual((None, "Машина"), dialects.split_category("Машина"))

    def test_embedded_slash_is_not_a_separator(self) -> None:
        self.assertEqual(
            ("Отпуск", "2023 France/Switzeland"),
            dialects.split_category("Отпуск / 2023 France/Switzeland"),
        )


class ReadRowsTest(unittest.TestCase):
    def test_full_dialect_kinds(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        self.assertEqual(5, len(rows))
        self.assertEqual(
            ["outcome", "outcome", "income", "transfer", "outcome"],
            [r.kind for r in rows],
        )

    def test_full_dialect_does_not_classify_everything_as_transfer(self) -> None:
        """Regression: both account names are populated on every row of the
        real full dump, so a presence-based rule marks all rows transfers."""
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        self.assertNotEqual(
            {"transfer"}, {r.kind for r in rows}, "kind must key on amounts"
        )

    def test_month_dialect_kinds_match_full_dialect(self) -> None:
        full = dialects.read_rows(FIXTURES / "full_dialect.csv")
        month = dialects.read_rows(FIXTURES / "month_dialect.csv")
        self.assertEqual([r.kind for r in full], [r.kind for r in month])

    def test_dialects_normalise_identically(self) -> None:
        full = dialects.read_rows(FIXTURES / "full_dialect.csv")
        month = dialects.read_rows(FIXTURES / "month_dialect.csv")
        self.assertEqual(full, month)

    def test_unused_side_is_blanked(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        expense = rows[0]
        self.assertEqual("", expense.income_account)
        self.assertEqual("", expense.income_currency)
        self.assertEqual(0, expense.income_minor)

    def test_income_row_blanks_outcome_side(self) -> None:
        income = dialects.read_rows(FIXTURES / "full_dialect.csv")[2]
        self.assertEqual("", income.outcome_account)
        self.assertEqual(767096, income.income_minor)

    def test_transfer_keeps_both_sides(self) -> None:
        tr = dialects.read_rows(FIXTURES / "full_dialect.csv")[3]
        self.assertEqual(2500000, tr.outcome_minor)
        self.assertEqual("RUB", tr.outcome_currency)
        self.assertEqual(33436, tr.income_minor)
        self.assertEqual("USD", tr.income_currency)


class NormalisationTest(unittest.TestCase):
    def test_collapses_internal_whitespace(self) -> None:
        self.assertEqual("Циан. Занесены", dialects.normalise_text("Циан.  Занесены"))

    def test_trims_and_handles_none(self) -> None:
        self.assertEqual("Zoom", dialects.normalise_text("  Zoom "))
        self.assertEqual("", dialects.normalise_text(None))

    def test_currency_kept_when_the_account_declares_one(self) -> None:
        self.assertEqual("EUR", dialects.account_currency("(EUR) Bunq", "EUR"))

    def test_currency_dropped_when_the_account_is_silent(self) -> None:
        """`Debts` is stamped EUR by one export and RUB by the other."""
        self.assertEqual("", dialects.account_currency("Debts", "EUR"))
        self.assertEqual("", dialects.account_currency("Debts", "RUB"))

    def test_currency_dropped_for_an_empty_account(self) -> None:
        self.assertEqual("", dialects.account_currency("", "EUR"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_dialects -v`
Expected: FAIL — `ImportError: cannot import name 'dialects'`

- [ ] **Step 4: Write the implementation**

Create `finance/dialects.py`:

```python
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
    cleaned = text.strip().replace(" ", "").replace(" ", "").replace(",", ".")
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_dialects -v`
Expected: PASS, 15 tests

- [ ] **Step 6: Verify against the real files**

Run:

```bash
python3 -c "
from pathlib import Path
from collections import Counter
from finance import dialects
rows = dialects.read_rows(Path('$HOME/Downloads/zen_2026-09-03_dumpof_transactions_1788441684.csv'))
print(len(rows), Counter(r.kind for r in rows))
"
```

Expected: `17397 Counter({'outcome': 13675, 'transfer': 2381, 'income': 1341})`

- [ ] **Step 7: Commit**

```bash
git add finance/dialects.py finance/tests/test_dialects.py finance/tests/fixtures
git commit -m "feat(finance): two-dialect CSV reader with amount-based kind derivation"
```

---

### Task 3: Content fingerprint with occurrence ordinals

**Files:**
- Create: `finance/fingerprint.py`
- Test: `finance/tests/test_fingerprint.py`

**Interfaces:**
- Consumes: `finance.dialects.RawRow`
- Produces:
  - `finance.fingerprint.canonical(row: RawRow) -> str`
  - `finance.fingerprint.assign_ids(rows: list[RawRow]) -> list[tuple[str, RawRow]]` — returns `(id, row)` pairs; identical rows get stable distinct ids by occurrence order

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_fingerprint.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path

from finance import dialects, fingerprint

FIXTURES = Path(__file__).parent / "fixtures"


class CanonicalTest(unittest.TestCase):
    def test_excludes_changed_at(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        base = rows[3]
        moved = dialects.RawRow(**{**base.__dict__, "changed_at": "2099-01-01 00:00:00"})
        self.assertEqual(fingerprint.canonical(base), fingerprint.canonical(moved))

    def test_excludes_category(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        base = rows[3]
        recategorised = dialects.RawRow(**{**base.__dict__, "category": "Другое"})
        self.assertEqual(
            fingerprint.canonical(base), fingerprint.canonical(recategorised)
        )

    def test_amount_change_produces_different_canonical(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        base = rows[3]
        cheaper = dialects.RawRow(**{**base.__dict__, "outcome_minor": 1})
        self.assertNotEqual(fingerprint.canonical(base), fingerprint.canonical(cheaper))


class AssignIdsTest(unittest.TestCase):
    def test_identical_rows_get_distinct_stable_ids(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        ids = [i for i, _ in fingerprint.assign_ids(rows)]
        self.assertEqual(len(ids), len(set(ids)), "no id collisions")

    def test_ids_are_stable_across_runs(self) -> None:
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        first = [i for i, _ in fingerprint.assign_ids(rows)]
        second = [i for i, _ in fingerprint.assign_ids(rows)]
        self.assertEqual(first, second)

    def test_ids_are_stable_when_input_order_changes(self) -> None:
        """created_at is unique per fixture row, so it identifies a row across
        orderings. Assigning ordinals by file position would fail this."""
        rows = dialects.read_rows(FIXTURES / "full_dialect.csv")
        forward = {r.created_at: i for i, r in fingerprint.assign_ids(rows)}
        backward = {
            r.created_at: i
            for i, r in fingerprint.assign_ids(list(reversed(rows)))
        }
        self.assertEqual(forward, backward)

    def test_ids_match_across_dialects(self) -> None:
        full = fingerprint.assign_ids(dialects.read_rows(FIXTURES / "full_dialect.csv"))
        month = fingerprint.assign_ids(
            dialects.read_rows(FIXTURES / "month_dialect.csv")
        )
        self.assertEqual([i for i, _ in full], [i for i, _ in month])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_fingerprint -v`
Expected: FAIL — `ImportError: cannot import name 'fingerprint'`

- [ ] **Step 3: Write the implementation**

Create `finance/fingerprint.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_fingerprint -v`
Expected: PASS, 8 tests

If `test_ids_are_stable_when_input_order_changes` fails, the ordinal is being assigned by file position rather than by `created_at`. Fix by assigning each row its id from its rank inside the sorted collision group rather than from a running counter.

- [ ] **Step 5: Verify against the real data**

These exact numbers were confirmed by prototyping the algorithm against the
real exports before this plan was written. Anything else means a defect.

```bash
python3 -c "
import glob
from pathlib import Path
from finance import dialects, fingerprint
home = Path.home()
full = dialects.read_rows(home / 'Downloads/zen_2026-09-03_dumpof_transactions_1788441684.csv')
ids = [i for i, _ in fingerprint.assign_ids(full)]
print('full rows / unique ids:', len(ids), len(set(ids)))
print('order-independent:', sorted(ids) == sorted(
    i for i, _ in fingerprint.assign_ids(list(reversed(full)))))
month = []
for f in sorted(glob.glob(str(home / 'Downloads/zen_*_from_month_*.csv'))):
    month.extend(dialects.read_rows(Path(f)))
mids = {i for i, _ in fingerprint.assign_ids(month)}
print('monthly rows:', len(month), 'ids not in full dump:', len(mids - set(ids)))
"
```

Expected, exactly:

```
full rows / unique ids: 17397 17397
order-independent: True
monthly rows: 8624 ids not in full dump: 0
```

The last line is the one that matters: it proves the two dialects fingerprint
the same transaction identically, so ingesting both never duplicates. If it is
non-zero, the whitespace or account-currency normalisation in Task 2 is wrong —
79 mismatches means `account_currency` is missing, 1 means `normalise_text` is.

- [ ] **Step 6: Commit**

```bash
git add finance/fingerprint.py finance/tests/test_fingerprint.py
git commit -m "feat(finance): content fingerprints with stable occurrence ordinals"
```

---

### Task 4: Idempotent ingest with range-scoped reconciliation

**Files:**
- Create: `finance/ingest.py`
- Modify: `finance/cli.py` (add `ingest` subcommand)
- Test: `finance/tests/test_ingest.py`

**Interfaces:**
- Consumes: `dialects.read_rows`, `fingerprint.assign_ids`, `db.connect`, `db.migrate`
- Produces:
  - `finance.ingest.IngestResult` — frozen dataclass with `rows_seen, rows_new, rows_updated, rows_deleted: int`
  - `finance.ingest.ingest_file(conn, path: Path) -> IngestResult`
  - `finance.ingest.ingest_paths(conn, paths: list[Path]) -> IngestResult` — sorted by filename, aggregates
  - `finance.ingest.resolve_account(conn, name: str, currency: str) -> int | None`
  - `finance.ingest.resolve_category(conn, label: str) -> int | None`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_ingest.py`:

```python
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from finance import db, ingest

FIXTURES = Path(__file__).parent / "fixtures"


class IngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "t.db")
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _live(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL"
        ).fetchone()[0]

    def test_first_ingest_inserts_every_row(self) -> None:
        result = ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        self.assertEqual(5, result.rows_seen)
        self.assertEqual(5, result.rows_new)
        self.assertEqual(5, self._live())

    def test_reingest_is_a_no_op(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        again = ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        self.assertEqual(0, again.rows_new)
        self.assertEqual(0, again.rows_updated)
        self.assertEqual(0, again.rows_deleted)
        self.assertEqual(5, self._live())

    def test_other_dialect_adds_nothing(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        cross = ingest.ingest_file(self.conn, FIXTURES / "month_dialect.csv")
        self.assertEqual(0, cross.rows_new)
        self.assertEqual(5, self._live())

    def test_accounts_and_categories_are_registered(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        names = {
            r["name"] for r in self.conn.execute("SELECT name FROM accounts")
        }
        self.assertIn("(EUR) Bunq", names)
        self.assertIn("(USD) Wise", names)
        row = self.conn.execute(
            "SELECT parent, leaf FROM categories WHERE full_name = ?",
            ("Отпуск / 2023 France/Switzeland",),
        ).fetchone()
        self.assertEqual("Отпуск", row["parent"])
        self.assertEqual("2023 France/Switzeland", row["leaf"])

    def test_recategorisation_updates_in_place(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        edited = self.root / "edited.csv"
        text = (FIXTURES / "full_dialect.csv").read_text(encoding="utf-8")
        edited.write_text(text.replace("Корректировка", "Личные траты"), "utf-8")
        result = ingest.ingest_file(self.conn, edited)
        self.assertEqual(0, result.rows_new)
        self.assertEqual(1, result.rows_updated)
        self.assertEqual(5, self._live())

    def test_removed_row_is_soft_deleted_within_range(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        lines = (FIXTURES / "full_dialect.csv").read_text("utf-8").splitlines()
        trimmed = self.root / "trimmed.csv"
        trimmed.write_text("\n".join(lines[:-1]) + "\n", "utf-8")
        result = ingest.ingest_file(self.conn, trimmed)
        self.assertEqual(1, result.rows_deleted)
        self.assertEqual(4, self._live())

    def test_reconciliation_does_not_touch_rows_outside_the_range(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        header, *body = (
            (FIXTURES / "full_dialect.csv").read_text("utf-8").strip().splitlines()
        )
        narrow = self.root / "narrow.csv"
        narrow.write_text(f"{header}\n{body[0]}\n", "utf-8")
        result = ingest.ingest_file(self.conn, narrow)
        self.assertEqual(1, result.rows_deleted, "only the 07-02 twin is missing")
        self.assertEqual(4, self._live())

    def test_soft_deleted_row_revives_when_it_returns(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        lines = (FIXTURES / "full_dialect.csv").read_text("utf-8").splitlines()
        trimmed = self.root / "trimmed.csv"
        trimmed.write_text("\n".join(lines[:-1]) + "\n", "utf-8")
        ingest.ingest_file(self.conn, trimmed)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        self.assertEqual(5, self._live())

    def test_batch_is_recorded(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        row = self.conn.execute(
            "SELECT rows_seen, rows_new FROM import_batches"
        ).fetchone()
        self.assertEqual(5, row["rows_seen"])
        self.assertEqual(5, row["rows_new"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_ingest -v`
Expected: FAIL — `ImportError: cannot import name 'ingest'`

- [ ] **Step 3: Write the implementation**

Create `finance/ingest.py`:

```python
"""Load CSV dumps into the warehouse, idempotently.

Re-importing the same data must be a no-op, and re-importing an edited export
must bring the warehouse into line with it. That means three behaviours:

* rows keyed by content fingerprint, so an unchanged row is recognised;
* mutable fields (category, amounts-in-EUR, timestamps) updated in place;
* rows that vanished from a re-export soft-deleted — but only within the date
  range the file actually covers, so importing a single month never wipes the
  rest of the history.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import dialects, fingerprint

CSV_GLOB = "*.csv"
DEFAULT_DUMP_DIR = Path("data/dumps")


@dataclass(frozen=True)
class IngestResult:
    rows_seen: int = 0
    rows_new: int = 0
    rows_updated: int = 0
    rows_deleted: int = 0

    def merge(self, other: IngestResult) -> IngestResult:
        return IngestResult(
            self.rows_seen + other.rows_seen,
            self.rows_new + other.rows_new,
            self.rows_updated + other.rows_updated,
            self.rows_deleted + other.rows_deleted,
        )


def resolve_account(
    conn: sqlite3.Connection, name: str, currency: str
) -> int | None:
    """Return the id for `name`, inserting the account on first sight."""
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM accounts WHERE name = ?", (name,)
    ).fetchone()
    if row is not None:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO accounts (name, currency) VALUES (?, ?)", (name, currency)
    )
    return int(cursor.lastrowid)


def resolve_category(conn: sqlite3.Connection, label: str) -> int | None:
    """Return the id for `label`, inserting the category on first sight."""
    if not label:
        return None
    row = conn.execute(
        "SELECT id FROM categories WHERE full_name = ?", (label,)
    ).fetchone()
    if row is not None:
        return row["id"]
    parent, leaf = dialects.split_category(label)
    cursor = conn.execute(
        "INSERT INTO categories (full_name, parent, leaf) VALUES (?, ?, ?)",
        (label, parent, leaf),
    )
    return int(cursor.lastrowid)


def _upsert(
    conn: sqlite3.Connection,
    row_id: str,
    row: dialects.RawRow,
    source_file: str,
    now: str,
) -> str:
    """Insert or refresh one transaction. Returns 'new' or 'updated' or 'same'."""
    existing = conn.execute(
        "SELECT category_id, deleted_at FROM transactions WHERE id = ?", (row_id,)
    ).fetchone()
    category_id = resolve_category(conn, row.category)

    if existing is None:
        conn.execute(
            """
            INSERT INTO transactions (
              id, date, category_id, payee, comment,
              outcome_account_id, outcome_minor, outcome_currency,
              income_account_id, income_minor, income_currency,
              kind, created_at, changed_at, source_file, imported_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row_id,
                row.date,
                category_id,
                row.payee,
                row.comment,
                resolve_account(conn, row.outcome_account, row.outcome_currency),
                row.outcome_minor,
                row.outcome_currency,
                resolve_account(conn, row.income_account, row.income_currency),
                row.income_minor,
                row.income_currency,
                row.kind,
                row.created_at,
                row.changed_at,
                source_file,
                now,
            ),
        )
        return "new"

    unchanged = (
        existing["category_id"] == category_id and existing["deleted_at"] is None
    )
    conn.execute(
        """
        UPDATE transactions
           SET category_id = ?, changed_at = ?, source_file = ?,
               imported_at = ?, deleted_at = NULL
         WHERE id = ?
        """,
        (category_id, row.changed_at, source_file, now, row_id),
    )
    return "same" if unchanged else "updated"


def ingest_file(conn: sqlite3.Connection, path: Path) -> IngestResult:
    """Load one dump, then reconcile the date range it covers."""
    rows = dialects.read_rows(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source = path.name

    seen_ids: set[str] = set()
    new = updated = 0
    for row_id, row in fingerprint.assign_ids(rows):
        seen_ids.add(row_id)
        outcome = _upsert(conn, row_id, row, source, now)
        if outcome == "new":
            new += 1
        elif outcome == "updated":
            updated += 1

    deleted = 0
    if rows:
        dates = [r.date for r in rows]
        placeholders = ",".join("?" * len(seen_ids)) or "''"
        cursor = conn.execute(
            f"""
            UPDATE transactions
               SET deleted_at = ?
             WHERE deleted_at IS NULL
               AND date BETWEEN ? AND ?
               AND id NOT IN ({placeholders})
            """,
            (now, min(dates), max(dates), *seen_ids),
        )
        deleted = cursor.rowcount

    result = IngestResult(len(rows), new, updated, deleted)
    conn.execute(
        """
        INSERT INTO import_batches
          (ran_at, files, rows_seen, rows_new, rows_updated, rows_deleted)
        VALUES (?,?,?,?,?,?)
        """,
        (
            now,
            json.dumps([source]),
            result.rows_seen,
            result.rows_new,
            result.rows_updated,
            result.rows_deleted,
        ),
    )
    conn.commit()
    return result


def ingest_paths(conn: sqlite3.Connection, paths: list[Path]) -> IngestResult:
    """Ingest files in filename order so later files win on conflict."""
    total = IngestResult()
    for path in sorted(paths):
        total = total.merge(ingest_file(conn, path))
    return total


def discover(folder: Path) -> list[Path]:
    return sorted(folder.glob(CSV_GLOB))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_ingest -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Wire the CLI**

In `finance/cli.py`, add the import `from . import ingest` and register the subcommand inside `main` before `args = parser.parse_args(argv)`:

```python
    ingest_cmd = sub.add_parser("ingest", help="load CSV dumps into the warehouse")
    ingest_cmd.add_argument(
        "--from", dest="folder", type=Path, default=ingest.DEFAULT_DUMP_DIR
    )
    ingest_cmd.add_argument("--file", dest="files", type=Path, action="append")
```

and add this branch after the `init` branch:

```python
    if args.command == "ingest":
        conn = db.connect(args.db)
        db.migrate(conn)
        paths = args.files or ingest.discover(args.folder)
        if not paths:
            print(f"no CSV files found in {args.folder}")
            return 1
        result = ingest.ingest_paths(conn, paths)
        print(
            f"seen={result.rows_seen} new={result.rows_new} "
            f"updated={result.rows_updated} deleted={result.rows_deleted}"
        )
        return 0
```

- [ ] **Step 6: Load the real data and verify idempotency**

```bash
mkdir -p data/dumps
cp ~/Downloads/zen_2026-09-03_dumpof_transactions_1788441684.csv data/dumps/
python3 -m finance ingest
python3 -m finance ingest
```

Expected: first run `seen=17397 new=17397 updated=0 deleted=0`; **second run `seen=17397 new=0 updated=0 deleted=0`**.

Then confirm the counts:

```bash
python3 -m finance --db data/finance.db init >/dev/null
sqlite3 data/finance.db "SELECT kind, COUNT(*) FROM transactions WHERE deleted_at IS NULL GROUP BY kind;"
```

Expected: `income|1341`, `outcome|13675`, `transfer|2381`

- [ ] **Step 7: Commit**

```bash
git add finance/ingest.py finance/cli.py finance/tests/test_ingest.py
git commit -m "feat(finance): idempotent ingest with range-scoped reconciliation"
```

---

### Task 5: Account classification via accounts.toml

**Files:**
- Create: `finance/accounts.py`, `accounts.toml`
- Modify: `finance/cli.py` (add `accounts` subcommand)
- Test: `finance/tests/test_accounts.py`

**Interfaces:**
- Consumes: `db.connect`
- Produces:
  - `finance.accounts.KINDS: frozenset[str]` = `{"spending","cash","savings","investment","credit","debt"}`
  - `finance.accounts.guess_kind(name: str) -> str`
  - `finance.accounts.seed_toml(conn) -> str` — returns TOML text for every known account
  - `finance.accounts.apply_toml(conn, text: str) -> int` — returns rows updated

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_accounts.py`:

```python
from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from finance import accounts, db, ingest

FIXTURES = Path(__file__).parent / "fixtures"


class GuessKindTest(unittest.TestCase):
    def test_savings_from_russian_keywords(self) -> None:
        self.assertEqual("savings", accounts.guess_kind("(RUB) Тинькофф Накопительный"))
        self.assertEqual("savings", accounts.guess_kind("(RUB) Тинькофф депозит 8"))
        self.assertEqual("savings", accounts.guess_kind("Вклад промсвязьбанк"))

    def test_investment(self) -> None:
        self.assertEqual("investment", accounts.guess_kind("Брокерский счет"))
        self.assertEqual("investment", accounts.guess_kind("ИИС"))
        self.assertEqual("investment", accounts.guess_kind("Interactive brokers"))

    def test_credit_beats_spending(self) -> None:
        self.assertEqual("credit", accounts.guess_kind("(RUB) Тинькофф кредитка"))

    def test_cash(self) -> None:
        self.assertEqual("cash", accounts.guess_kind("(EUR) cash"))
        self.assertEqual("cash", accounts.guess_kind("(RUB) Наличные"))
        self.assertEqual("cash", accounts.guess_kind("(RUB) Домашняя кубышка"))

    def test_debt(self) -> None:
        self.assertEqual("debt", accounts.guess_kind("Debts"))

    def test_default_is_spending(self) -> None:
        self.assertEqual("spending", accounts.guess_kind("(EUR) Bunq"))

    def test_every_guess_is_a_valid_kind(self) -> None:
        for name in ("(EUR) Bunq", "ИИС", "Debts", "(EUR) cash"):
            self.assertIn(accounts.guess_kind(name), accounts.KINDS)


class TomlRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_seed_produces_parseable_toml_for_every_account(self) -> None:
        text = accounts.seed_toml(self.conn)
        parsed = tomllib.loads(text)
        self.assertIn("(EUR) Bunq", parsed["accounts"])
        self.assertEqual("spending", parsed["accounts"]["(EUR) Bunq"]["kind"])

    def test_apply_sets_kinds(self) -> None:
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        row = self.conn.execute(
            "SELECT kind FROM accounts WHERE name = ?", ("(RUB) Тинькофф депозит 8",)
        ).fetchone()
        self.assertEqual("savings", row["kind"])

    def test_apply_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            accounts.apply_toml(
                self.conn, '[accounts."(EUR) Bunq"]\nkind = "nonsense"\n'
            )

    def test_alias_of_links_accounts(self) -> None:
        accounts.apply_toml(
            self.conn,
            '[accounts."(EUR) Bunq"]\nkind = "spending"\n'
            '[accounts."(USD) Wise"]\nkind = "spending"\nalias_of = "(EUR) Bunq"\n',
        )
        row = self.conn.execute(
            "SELECT alias_of FROM accounts WHERE name = ?", ("(USD) Wise",)
        ).fetchone()
        target = self.conn.execute(
            "SELECT id FROM accounts WHERE name = ?", ("(EUR) Bunq",)
        ).fetchone()
        self.assertEqual(target["id"], row["alias_of"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_accounts -v`
Expected: FAIL — `ImportError: cannot import name 'accounts'`

- [ ] **Step 3: Write the implementation**

Create `finance/accounts.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_accounts -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Wire the CLI and generate the real file**

In `finance/cli.py` add `from . import accounts` and register:

```python
    accounts_cmd = sub.add_parser("accounts", help="manage account classification")
    accounts_cmd.add_argument("--seed", action="store_true")
    accounts_cmd.add_argument("--apply", action="store_true")
    accounts_cmd.add_argument("--path", type=Path, default=Path("accounts.toml"))
```

and the branch:

```python
    if args.command == "accounts":
        conn = db.connect(args.db)
        db.migrate(conn)
        if args.seed:
            args.path.write_text(accounts.seed_toml(conn), encoding="utf-8")
            print(f"wrote {args.path}")
        if args.apply:
            count = accounts.apply_toml(
                conn, args.path.read_text(encoding="utf-8")
            )
            print(f"applied {count} account(s)")
        return 0
```

Then generate and apply it:

```bash
python3 -m finance accounts --seed
python3 -m finance accounts --apply
sqlite3 data/finance.db "SELECT kind, COUNT(*) FROM accounts GROUP BY kind;"
```

Expected: all 48 accounts classified, none with a NULL kind.

**Then stop and ask the user to review `accounts.toml`** — the savings-rate number depends on it, and the near-duplicate accounts (`Карточка - Альфа` vs `(RUB) Карточка Альфа`, the eight `Тинькофф депозит` variants) need a human decision on `alias_of`.

- [ ] **Step 6: Commit**

```bash
git add finance/accounts.py finance/cli.py finance/tests/test_accounts.py accounts.toml
git commit -m "feat(finance): account classification via accounts.toml"
```

---

### Task 6: FX resolver — ECB layer and rate storage

**Files:**
- Create: `finance/fx.py`, `fx_overrides.toml`
- Test: `finance/tests/test_fx.py`

**Interfaces:**
- Consumes: `db.connect`
- Produces:
  - `finance.fx.ECB_URL: str` = `"https://api.frankfurter.dev/v1/{start}..{end}"`
  - `finance.fx.fetch_ecb(start: str, end: str, symbols: list[str], opener=urllib.request.urlopen) -> dict[str, dict[str, float]]` — `{date: {currency: eur_per_unit}}`
  - `finance.fx.store_rates(conn, rates: dict[str, dict[str, float]], source: str) -> int`
  - `finance.fx.rate_for(conn, date: str, currency: str) -> tuple[float, str] | None` — `(eur_per_unit, source)`
  - `finance.fx.to_eur_minor(amount_minor: int, eur_per_unit: float) -> int`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_fx.py`:

```python
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from finance import db, fx


def fake_opener(payload: dict) -> object:
    def _open(url: str, timeout: float = 0):  # noqa: ARG001
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    return _open


class FetchEcbTest(unittest.TestCase):
    def test_inverts_ecb_quotes_to_eur_per_unit(self) -> None:
        """ECB quotes EUR->X; the warehouse stores how many EUR one X buys."""
        payload = {
            "amount": 1.0,
            "base": "EUR",
            "rates": {"2021-01-04": {"USD": 1.2296, "RUB": 90.342}},
        }
        rates = fx.fetch_ecb(
            "2021-01-04", "2021-01-04", ["USD", "RUB"], opener=fake_opener(payload)
        )
        self.assertAlmostEqual(1 / 1.2296, rates["2021-01-04"]["USD"], places=9)
        self.assertAlmostEqual(1 / 90.342, rates["2021-01-04"]["RUB"], places=9)

    def test_missing_currency_is_simply_absent(self) -> None:
        payload = {"rates": {"2026-07-01": {"USD": 1.1383}}}
        rates = fx.fetch_ecb(
            "2026-07-01", "2026-07-01", ["USD", "RUB"], opener=fake_opener(payload)
        )
        self.assertNotIn("RUB", rates["2026-07-01"])


class StoreAndReadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_eur_is_always_one_without_being_stored(self) -> None:
        self.assertEqual((1.0, "base"), fx.rate_for(self.conn, "2024-01-01", "EUR"))

    def test_store_then_read_back(self) -> None:
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.9}}, "ecb")
        self.assertEqual((0.9, "ecb"), fx.rate_for(self.conn, "2024-01-02", "USD"))

    def test_unknown_date_returns_none(self) -> None:
        self.assertIsNone(fx.rate_for(self.conn, "2024-01-02", "USD"))

    def test_higher_priority_source_wins_on_conflict(self) -> None:
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.8}}, "filled")
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.9}}, "ecb")
        self.assertEqual((0.9, "ecb"), fx.rate_for(self.conn, "2024-01-02", "USD"))

    def test_lower_priority_source_does_not_overwrite(self) -> None:
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.9}}, "ecb")
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.1}}, "filled")
        self.assertEqual((0.9, "ecb"), fx.rate_for(self.conn, "2024-01-02", "USD"))


class ConversionTest(unittest.TestCase):
    def test_rounds_to_nearest_cent(self) -> None:
        self.assertEqual(90, fx.to_eur_minor(100, 0.895))

    def test_zero_stays_zero(self) -> None:
        self.assertEqual(0, fx.to_eur_minor(0, 0.9))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_fx -v`
Expected: FAIL — `ImportError: cannot import name 'fx'`

- [ ] **Step 3: Write the implementation**

Create `finance/fx.py`:

```python
"""Resolve every amount to EUR at its own transaction date.

The ECB is the best source but does not cover this dataset: it delisted RUB in
March 2022 and never published KZT. Four layers fill the gap, in descending
order of trust, and each rate records which layer produced it so reports can
disclose how much of a total rests on inference:

  ecb      published reference rates
  implied  derived from the owner's own cross-currency transfers
  manual   `fx_overrides.toml`
  filled   interpolated or carried forward between known points

`rate_for` returns EUR per one unit of the currency — 1 USD is about 0.88 EUR —
which is the inverse of how the ECB quotes it.
"""

from __future__ import annotations

import json
import sqlite3
import tomllib
import urllib.request
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ECB_URL = "https://api.frankfurter.dev/v1/{start}..{end}"
BASE_CURRENCY = "EUR"
OVERRIDES_PATH = Path("fx_overrides.toml")
REQUEST_TIMEOUT = 30.0

# Higher wins when two layers claim the same date and currency.
SOURCE_PRIORITY: dict[str, int] = {"filled": 0, "implied": 1, "manual": 2, "ecb": 3}


def fetch_ecb(
    start: str,
    end: str,
    symbols: list[str],
    opener=urllib.request.urlopen,
) -> dict[str, dict[str, float]]:
    """Fetch ECB rates for a date range, inverted to EUR-per-unit."""
    url = ECB_URL.format(start=start, end=end)
    url += f"?base={BASE_CURRENCY}&symbols={','.join(symbols)}"
    with opener(url, timeout=REQUEST_TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))

    result: dict[str, dict[str, float]] = {}
    for date, quotes in payload.get("rates", {}).items():
        result[date] = {
            currency: 1.0 / value
            for currency, value in quotes.items()
            if value
        }
    return result


def store_rates(
    conn: sqlite3.Connection, rates: dict[str, dict[str, float]], source: str
) -> int:
    """Insert rates, keeping the higher-priority source on conflict."""
    written = 0
    for date, quotes in rates.items():
        for currency, value in quotes.items():
            existing = conn.execute(
                "SELECT source FROM fx_rates WHERE date = ? AND currency = ?",
                (date, currency),
            ).fetchone()
            if existing is not None and SOURCE_PRIORITY.get(
                existing["source"], -1
            ) >= SOURCE_PRIORITY.get(source, -1):
                continue
            conn.execute(
                """
                INSERT INTO fx_rates (date, currency, eur_per_unit, source)
                VALUES (?,?,?,?)
                ON CONFLICT(date, currency)
                DO UPDATE SET eur_per_unit = excluded.eur_per_unit,
                              source = excluded.source
                """,
                (date, currency, value, source),
            )
            written += 1
    conn.commit()
    return written


def rate_for(
    conn: sqlite3.Connection, date: str, currency: str
) -> tuple[float, str] | None:
    """Return `(eur_per_unit, source)` for `currency` on `date`."""
    if currency == BASE_CURRENCY:
        return 1.0, "base"
    row = conn.execute(
        "SELECT eur_per_unit, source FROM fx_rates WHERE date = ? AND currency = ?",
        (date, currency),
    ).fetchone()
    if row is None:
        return None
    return row["eur_per_unit"], row["source"]


def to_eur_minor(amount_minor: int, eur_per_unit: float) -> int:
    """Convert minor units to EUR minor units, rounding half up."""
    converted = Decimal(amount_minor) * Decimal(str(eur_per_unit))
    return int(converted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, dict[str, float]]:
    """Read manual rates. Missing file means no overrides."""
    if not path.exists():
        return {}
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        date: {c: float(v) for c, v in quotes.items()}
        for date, quotes in parsed.get("rates", {}).items()
    }
```

Create `fx_overrides.toml`:

```toml
# Manual EUR-per-unit FX rates, highest trust after the ECB.
#
# Use this for dates no other layer can reach. One table per date; each key is
# a currency and each value is how many EUR one unit of it buys.
#
# [rates."2024-06-15"]
# KZT = 0.00205
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_fx -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add finance/fx.py finance/tests/test_fx.py fx_overrides.toml
git commit -m "feat(finance): FX rate storage with layered source priority"
```

---

### Task 7: FX resolver — implied rates, filling, and EUR materialisation

**Files:**
- Modify: `finance/fx.py`, `finance/cli.py`
- Test: `finance/tests/test_fx_resolve.py`

**Interfaces:**
- Consumes: `fx.store_rates`, `fx.rate_for`, `fx.to_eur_minor`, `fx.fetch_ecb`, `fx.load_overrides`
- Produces:
  - `finance.fx.implied_rates(conn) -> dict[str, dict[str, float]]`
  - `finance.fx.fill_gaps(conn, currencies: list[str], start: str, end: str) -> int`
  - `finance.fx.materialise(conn) -> tuple[int, int]` — `(converted, unresolved)`
  - `finance.fx.refresh(conn, opener=urllib.request.urlopen) -> dict[str, int]`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_fx_resolve.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import db, fx, ingest

FIXTURES = Path(__file__).parent / "fixtures"


class ImpliedRatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_derives_unknown_side_from_known_side(self) -> None:
        """25000 RUB -> 334.36 USD, with USD known, pins RUB."""
        fx.store_rates(self.conn, {"2026-07-04": {"USD": 0.9}}, "ecb")
        implied = fx.implied_rates(self.conn)
        self.assertIn("RUB", implied["2026-07-04"])
        expected = (334.36 * 0.9) / 25000
        self.assertAlmostEqual(expected, implied["2026-07-04"]["RUB"], places=9)

    def test_eur_side_needs_no_prior_rate(self) -> None:
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, kind, outcome_minor, outcome_currency,
               income_minor, income_currency)
            VALUES ('x','2026-07-06','transfer',100000,'RUB',100,'EUR')
            """
        )
        implied = fx.implied_rates(self.conn)
        self.assertAlmostEqual(0.001, implied["2026-07-06"]["RUB"], places=9)

    def test_returns_nothing_when_neither_side_is_known(self) -> None:
        implied = fx.implied_rates(self.conn)
        self.assertEqual({}, implied)


class FillGapsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_interpolates_between_known_points(self) -> None:
        fx.store_rates(
            self.conn, {"2024-01-01": {"USD": 0.90}, "2024-01-03": {"USD": 0.92}}, "ecb"
        )
        fx.fill_gaps(self.conn, ["USD"], "2024-01-01", "2024-01-03")
        value, source = fx.rate_for(self.conn, "2024-01-02", "USD")
        self.assertAlmostEqual(0.91, value, places=6)
        self.assertEqual("filled", source)

    def test_carries_forward_past_the_last_known_point(self) -> None:
        fx.store_rates(self.conn, {"2024-01-01": {"USD": 0.90}}, "ecb")
        fx.fill_gaps(self.conn, ["USD"], "2024-01-01", "2024-01-03")
        self.assertEqual((0.90, "filled"), fx.rate_for(self.conn, "2024-01-03", "USD"))

    def test_carries_backward_before_the_first_known_point(self) -> None:
        fx.store_rates(self.conn, {"2024-01-03": {"USD": 0.92}}, "ecb")
        fx.fill_gaps(self.conn, ["USD"], "2024-01-01", "2024-01-03")
        self.assertEqual((0.92, "filled"), fx.rate_for(self.conn, "2024-01-01", "USD"))

    def test_does_not_overwrite_real_rates(self) -> None:
        fx.store_rates(self.conn, {"2024-01-02": {"USD": 0.5}}, "ecb")
        fx.fill_gaps(self.conn, ["USD"], "2024-01-01", "2024-01-03")
        self.assertEqual((0.5, "ecb"), fx.rate_for(self.conn, "2024-01-02", "USD"))


class MaterialiseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_eur_rows_convert_without_any_rate_table(self) -> None:
        converted, unresolved = fx.materialise(self.conn)
        row = self.conn.execute(
            "SELECT outcome_eur_minor, fx_source FROM transactions "
            "WHERE date='2026-07-02' LIMIT 1"
        ).fetchone()
        self.assertEqual(420, row["outcome_eur_minor"])
        self.assertEqual("base", row["fx_source"])
        self.assertGreater(converted, 0)

    def test_unresolved_rows_are_counted_and_left_null(self) -> None:
        _, unresolved = fx.materialise(self.conn)
        self.assertGreater(unresolved, 0, "RUB rows have no rate yet")
        row = self.conn.execute(
            "SELECT income_eur_minor FROM transactions WHERE date='2026-07-03'"
        ).fetchone()
        self.assertIsNone(row["income_eur_minor"])

    def test_records_the_weaker_source_for_a_transfer(self) -> None:
        fx.store_rates(self.conn, {"2026-07-04": {"USD": 0.9}}, "ecb")
        fx.store_rates(self.conn, {"2026-07-04": {"RUB": 0.01}}, "filled")
        fx.materialise(self.conn)
        row = self.conn.execute(
            "SELECT fx_source FROM transactions WHERE date='2026-07-04'"
        ).fetchone()
        self.assertEqual("filled", row["fx_source"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_fx_resolve -v`
Expected: FAIL — `AttributeError: module 'finance.fx' has no attribute 'implied_rates'`

- [ ] **Step 3: Write the implementation**

Append to `finance/fx.py`:

```python
def implied_rates(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Derive rates from cross-currency transfers.

    A transfer of 25 000 RUB that arrived as 334.36 USD is an observation of
    the rate actually realised that day. When one side's EUR rate is already
    known, the other side follows.
    """
    rows = conn.execute(
        """
        SELECT date, outcome_minor, outcome_currency, income_minor, income_currency
          FROM transactions
         WHERE kind = 'transfer'
           AND deleted_at IS NULL
           AND outcome_currency <> income_currency
           AND outcome_minor > 0 AND income_minor > 0
        """
    ).fetchall()

    derived: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        out_rate = rate_for(conn, row["date"], row["outcome_currency"])
        in_rate = rate_for(conn, row["date"], row["income_currency"])
        if out_rate is not None and in_rate is None:
            eur = row["outcome_minor"] * out_rate[0]
            value = eur / row["income_minor"]
            currency = row["income_currency"]
        elif in_rate is not None and out_rate is None:
            eur = row["income_minor"] * in_rate[0]
            value = eur / row["outcome_minor"]
            currency = row["outcome_currency"]
        else:
            continue
        derived.setdefault(row["date"], {}).setdefault(currency, []).append(value)

    return {
        date: {c: sum(values) / len(values) for c, values in quotes.items()}
        for date, quotes in derived.items()
    }


def _dates_between(start: str, end: str) -> list[str]:
    from datetime import date as _date, timedelta

    first = _date.fromisoformat(start)
    last = _date.fromisoformat(end)
    span = (last - first).days
    return [(first + timedelta(days=n)).isoformat() for n in range(span + 1)]


def fill_gaps(
    conn: sqlite3.Connection, currencies: list[str], start: str, end: str
) -> int:
    """Interpolate between known rates, carrying the ends outward."""
    filled = 0
    for currency in currencies:
        if currency == BASE_CURRENCY:
            continue
        known = {
            row["date"]: row["eur_per_unit"]
            for row in conn.execute(
                "SELECT date, eur_per_unit FROM fx_rates WHERE currency = ? "
                "ORDER BY date",
                (currency,),
            )
        }
        if not known:
            continue
        anchors = sorted(known)
        gaps: dict[str, dict[str, float]] = {}
        for day in _dates_between(start, end):
            if day in known:
                continue
            before = [a for a in anchors if a <= day]
            after = [a for a in anchors if a >= day]
            if before and after:
                lo, hi = before[-1], after[0]
                span = (
                    _dates_between(lo, hi).index(hi) if lo != hi else 0
                )
                offset = _dates_between(lo, day).index(day)
                weight = offset / span if span else 0.0
                value = known[lo] + (known[hi] - known[lo]) * weight
            elif before:
                value = known[before[-1]]
            else:
                value = known[after[0]]
            gaps.setdefault(day, {})[currency] = value
        filled += store_rates(conn, gaps, "filled")
    return filled


def materialise(conn: sqlite3.Connection) -> tuple[int, int]:
    """Write EUR amounts onto every transaction. Returns (converted, unresolved)."""
    rows = conn.execute(
        """
        SELECT id, date, outcome_minor, outcome_currency,
               income_minor, income_currency
          FROM transactions
         WHERE deleted_at IS NULL
        """
    ).fetchall()

    converted = unresolved = 0
    for row in rows:
        sources: list[str] = []
        out_eur = in_eur = None
        missing = False

        if row["outcome_minor"]:
            found = rate_for(conn, row["date"], row["outcome_currency"])
            if found is None:
                missing = True
            else:
                out_eur = to_eur_minor(row["outcome_minor"], found[0])
                sources.append(found[1])
        if row["income_minor"]:
            found = rate_for(conn, row["date"], row["income_currency"])
            if found is None:
                missing = True
            else:
                in_eur = to_eur_minor(row["income_minor"], found[0])
                sources.append(found[1])

        if missing:
            unresolved += 1
            continue
        weakest = min(sources, key=lambda s: SOURCE_PRIORITY.get(s, 99)) if sources else None
        conn.execute(
            "UPDATE transactions SET outcome_eur_minor = ?, income_eur_minor = ?, "
            "fx_source = ? WHERE id = ?",
            (out_eur, in_eur, weakest, row["id"]),
        )
        converted += 1
    conn.commit()
    return converted, unresolved


def refresh(conn: sqlite3.Connection, opener=urllib.request.urlopen) -> dict[str, int]:
    """Run every layer in order, then materialise EUR amounts."""
    span = conn.execute(
        "SELECT MIN(date) AS lo, MAX(date) AS hi FROM transactions "
        "WHERE deleted_at IS NULL"
    ).fetchone()
    if span is None or span["lo"] is None:
        return {"ecb": 0, "implied": 0, "manual": 0, "filled": 0, "unresolved": 0}

    currencies = [
        row["currency"]
        for row in conn.execute(
            "SELECT DISTINCT outcome_currency AS currency FROM transactions "
            "WHERE outcome_currency <> '' UNION "
            "SELECT DISTINCT income_currency FROM transactions "
            "WHERE income_currency <> ''"
        )
        if row["currency"] != BASE_CURRENCY
    ]

    counts = {
        "ecb": store_rates(
            conn, fetch_ecb(span["lo"], span["hi"], currencies, opener), "ecb"
        )
    }
    counts["implied"] = store_rates(conn, implied_rates(conn), "implied")
    counts["manual"] = store_rates(conn, load_overrides(), "manual")
    counts["filled"] = fill_gaps(conn, currencies, span["lo"], span["hi"])
    converted, unresolved = materialise(conn)
    counts["converted"] = converted
    counts["unresolved"] = unresolved
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_fx_resolve -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Wire the CLI and run against real data**

In `finance/cli.py` add `from . import fx` and register:

```python
    fx_cmd = sub.add_parser("fx", help="refresh exchange rates and EUR amounts")
    fx_cmd.add_argument("--refresh", action="store_true")
```

and the branch:

```python
    if args.command == "fx":
        conn = db.connect(args.db)
        db.migrate(conn)
        counts = fx.refresh(conn)
        for key, value in counts.items():
            print(f"{key}={value}")
        return 0
```

Run: `python3 -m finance fx --refresh`

Expected: `unresolved` should be **0 or very close to it**. Then audit precision:

```bash
sqlite3 data/finance.db "SELECT fx_source, COUNT(*) FROM transactions WHERE deleted_at IS NULL GROUP BY fx_source ORDER BY 2 DESC;"
```

Expected: `base` dominant, then `ecb`, with `implied`/`filled` a small minority. If `unresolved > 0`, list the offending currency-dates and add them to `fx_overrides.toml`.

- [ ] **Step 6: Commit**

```bash
git add finance/fx.py finance/cli.py finance/tests/test_fx_resolve.py
git commit -m "feat(finance): implied rates, gap filling, and EUR materialisation"
```

---

### Task 8: Reporting views and coverage verification

**Files:**
- Modify: `finance/schema.sql`
- Create: `finance/verify.py`
- Modify: `finance/cli.py`
- Test: `finance/tests/test_views.py`, `finance/tests/test_verify.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - Views `v_transactions`, `v_spend`, `v_income`, `v_monthly`
  - `finance.verify.Coverage` — frozen dataclass `first_month, last_month: str`, `months: int`, `missing: list[str]`
  - `finance.verify.coverage(conn) -> Coverage`
  - `finance.verify.fx_precision(conn) -> dict[str, float]` — share of absolute EUR volume per `fx_source`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_views.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import accounts, db, fx, ingest

FIXTURES = Path(__file__).parent / "fixtures"


class ViewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        fx.store_rates(self.conn, {"2026-07-03": {"RUB": 0.01}}, "ecb")
        fx.store_rates(
            self.conn, {"2026-07-04": {"RUB": 0.01, "USD": 0.9}}, "ecb"
        )
        fx.materialise(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_spend_excludes_transfers(self) -> None:
        kinds = {
            r["kind"] for r in self.conn.execute("SELECT kind FROM v_spend")
        }
        self.assertEqual({"outcome"}, kinds)

    def test_spend_excludes_korrektirovka(self) -> None:
        labels = {
            r["category"] for r in self.conn.execute("SELECT category FROM v_spend")
        }
        self.assertNotIn("Корректировка", labels)

    def test_spend_includes_ordinary_expenses(self) -> None:
        count = self.conn.execute("SELECT COUNT(*) c FROM v_spend").fetchone()["c"]
        self.assertEqual(2, count, "two coffees only")

    def test_income_flags_passive_interest(self) -> None:
        row = self.conn.execute(
            "SELECT is_passive FROM v_income WHERE category = 'проценты'"
        ).fetchone()
        self.assertEqual(1, row["is_passive"])

    def test_transactions_view_splits_category(self) -> None:
        row = self.conn.execute(
            "SELECT category_parent, category_leaf FROM v_transactions "
            "WHERE date = '2026-07-04'"
        ).fetchone()
        self.assertEqual("Отпуск", row["category_parent"])
        self.assertEqual("2023 France/Switzeland", row["category_leaf"])

    def test_monthly_aggregates_by_month(self) -> None:
        row = self.conn.execute(
            "SELECT month, SUM(spend_eur) s FROM v_monthly GROUP BY month"
        ).fetchone()
        self.assertEqual("2026-07", row["month"])
        self.assertAlmostEqual(8.40, row["s"], places=2)

    def test_deleted_rows_are_absent_from_every_view(self) -> None:
        self.conn.execute("UPDATE transactions SET deleted_at = 'x'")
        for view in ("v_transactions", "v_spend", "v_income", "v_monthly"):
            count = self.conn.execute(f"SELECT COUNT(*) c FROM {view}").fetchone()["c"]
            self.assertEqual(0, count, view)


if __name__ == "__main__":
    unittest.main()
```

Create `finance/tests/test_verify.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import db, ingest, verify

FIXTURES = Path(__file__).parent / "fixtures"


class CoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, date: str) -> None:
        self.conn.execute(
            "INSERT INTO transactions (id, date, kind, outcome_minor, "
            "outcome_currency) VALUES (?,?,'outcome',100,'EUR')",
            (date, date),
        )

    def test_reports_span_and_no_gaps(self) -> None:
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        result = verify.coverage(self.conn)
        self.assertEqual("2026-07", result.first_month)
        self.assertEqual("2026-07", result.last_month)
        self.assertEqual([], result.missing)

    def test_names_the_missing_month(self) -> None:
        self._add("2024-01-05")
        self._add("2024-03-05")
        self.conn.commit()
        self.assertEqual(["2024-02"], verify.coverage(self.conn).missing)

    def test_empty_database_reports_no_months(self) -> None:
        result = verify.coverage(self.conn)
        self.assertEqual(0, result.months)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest finance.tests.test_views finance.tests.test_verify -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: v_spend` and `cannot import name 'verify'`

- [ ] **Step 3: Add the views to the schema**

Append to `finance/schema.sql`:

```sql
DROP VIEW IF EXISTS v_transactions;
CREATE VIEW v_transactions AS
SELECT
  t.id, t.date, substr(t.date, 1, 7) AS month, t.kind,
  c.full_name AS category, c.parent AS category_parent, c.leaf AS category_leaf,
  t.payee, t.comment,
  oa.name AS outcome_account, oa.kind AS outcome_account_kind,
  t.outcome_minor / 100.0 AS outcome, t.outcome_currency,
  t.outcome_eur_minor / 100.0 AS outcome_eur,
  ia.name AS income_account, ia.kind AS income_account_kind,
  t.income_minor / 100.0 AS income, t.income_currency,
  t.income_eur_minor / 100.0 AS income_eur,
  t.fx_source
FROM transactions t
LEFT JOIN categories c  ON c.id  = t.category_id
LEFT JOIN accounts   oa ON oa.id = t.outcome_account_id
LEFT JOIN accounts   ia ON ia.id = t.income_account_id
WHERE t.deleted_at IS NULL;

DROP VIEW IF EXISTS v_spend;
CREATE VIEW v_spend AS
SELECT * FROM v_transactions
WHERE kind = 'outcome'
  AND (category IS NULL OR category <> 'Корректировка')
  AND (outcome_account_kind IS NULL
       OR outcome_account_kind NOT IN ('savings', 'investment'));

DROP VIEW IF EXISTS v_income;
CREATE VIEW v_income AS
SELECT *, CASE WHEN category = 'проценты' THEN 1 ELSE 0 END AS is_passive
FROM v_transactions
WHERE kind = 'income'
  AND (category IS NULL OR category <> 'Корректировка');

DROP VIEW IF EXISTS v_monthly;
CREATE VIEW v_monthly AS
SELECT month,
       COALESCE(category, '(uncategorised)') AS category,
       category_parent,
       SUM(COALESCE(outcome_eur, 0)) AS spend_eur,
       COUNT(*) AS transactions
FROM v_spend
GROUP BY month, category, category_parent;
```

- [ ] **Step 4: Write verify.py**

Create `finance/verify.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_views finance.tests.test_verify -v`
Expected: PASS, 10 tests

- [ ] **Step 6: Wire the CLI and check the real database**

In `finance/cli.py` add `from . import verify` and register a `verify` parser plus a `query` parser:

```python
    sub.add_parser("verify", help="report coverage and FX precision")
    query_cmd = sub.add_parser("query", help="run ad-hoc SQL against the warehouse")
    query_cmd.add_argument("sql")
```

and the branches:

```python
    if args.command == "verify":
        conn = db.connect(args.db)
        db.migrate(conn)
        cov = verify.coverage(conn)
        print(f"months: {cov.months} ({cov.first_month} → {cov.last_month})")
        print(f"missing: {', '.join(cov.missing) if cov.missing else 'none'}")
        for source, share in sorted(
            verify.fx_precision(conn).items(), key=lambda kv: -kv[1]
        ):
            print(f"  fx {source}: {share:.1%}")
        return 0

    if args.command == "query":
        conn = db.connect(args.db)
        for row in conn.execute(args.sql):
            print("\t".join("" if v is None else str(v) for v in tuple(row)))
        return 0
```

Run: `python3 -m finance init && python3 -m finance verify`

Expected: `months: 155 (2013-10 → 2026-08)`, `missing: none`, and an FX breakdown dominated by `base` and `ecb`.

- [ ] **Step 7: Commit**

```bash
git add finance/schema.sql finance/verify.py finance/cli.py finance/tests/test_views.py finance/tests/test_verify.py
git commit -m "feat(finance): reporting views, coverage verification, ad-hoc query"
```

---

### Task 9: Cashflow analysis

**Files:**
- Create: `finance/analysis/__init__.py`, `finance/analysis/cashflow.py`
- Test: `finance/tests/test_cashflow.py`

**Interfaces:**
- Consumes: views `v_spend`, `v_income`, table `accounts`
- Produces:
  - `finance.analysis.cashflow.MonthRow` — frozen dataclass `month: str`, `earned_eur, passive_eur, spend_eur, net_eur: float`, `savings_rate: float | None`
  - `finance.analysis.cashflow.monthly(conn, since: str | None = None) -> list[MonthRow]`
  - `finance.analysis.cashflow.trailing_mean(rows: list[MonthRow], field: str, window: int) -> list[float | None]`
  - `finance.analysis.cashflow.net_flow_by_account(conn) -> list[dict]` — each with `account, kind, net_eur, is_true_balance: bool`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_cashflow.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import accounts, db, fx, ingest
from finance.analysis import cashflow

FIXTURES = Path(__file__).parent / "fixtures"


class MonthlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        fx.store_rates(
            self.conn,
            {"2026-07-03": {"RUB": 0.01}, "2026-07-04": {"RUB": 0.01, "USD": 0.9}},
            "ecb",
        )
        fx.materialise(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_spend_sums_only_real_expenses(self) -> None:
        row = cashflow.monthly(self.conn)[0]
        self.assertAlmostEqual(8.40, row.spend_eur, places=2)

    def test_interest_counts_as_passive_not_earned(self) -> None:
        row = cashflow.monthly(self.conn)[0]
        self.assertAlmostEqual(76.71, row.passive_eur, places=2)
        self.assertAlmostEqual(0.0, row.earned_eur, places=2)

    def test_net_is_income_minus_spend(self) -> None:
        row = cashflow.monthly(self.conn)[0]
        self.assertAlmostEqual(
            row.earned_eur + row.passive_eur - row.spend_eur, row.net_eur, places=2
        )

    def test_savings_rate_is_none_without_income(self) -> None:
        self.conn.execute("DELETE FROM transactions WHERE kind = 'income'")
        self.conn.commit()
        self.assertIsNone(cashflow.monthly(self.conn)[0].savings_rate)

    def test_since_filters_earlier_months(self) -> None:
        self.assertEqual([], cashflow.monthly(self.conn, since="2030-01"))


class TrailingMeanTest(unittest.TestCase):
    def _rows(self, values: list[float]) -> list[cashflow.MonthRow]:
        return [
            cashflow.MonthRow(f"2024-{i + 1:02d}", 0.0, 0.0, v, 0.0, None)
            for i, v in enumerate(values)
        ]

    def test_is_none_until_the_window_is_full(self) -> None:
        result = cashflow.trailing_mean(self._rows([1, 2, 3]), "spend_eur", 3)
        self.assertIsNone(result[0])
        self.assertIsNone(result[1])
        self.assertAlmostEqual(2.0, result[2])

    def test_slides_forward(self) -> None:
        result = cashflow.trailing_mean(self._rows([1, 2, 3, 4]), "spend_eur", 3)
        self.assertAlmostEqual(3.0, result[3])


class NetFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        fx.materialise(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_is_not_a_true_balance_without_an_opening_figure(self) -> None:
        rows = cashflow.net_flow_by_account(self.conn)
        bunq = next(r for r in rows if r["account"] == "(EUR) Bunq")
        self.assertFalse(bunq["is_true_balance"])

    def test_becomes_a_true_balance_once_an_opening_figure_exists(self) -> None:
        self.conn.execute(
            "UPDATE accounts SET opening_balance_minor = 1000, "
            "opening_date = '2026-07-01' WHERE name = '(EUR) Bunq'"
        )
        self.conn.commit()
        rows = cashflow.net_flow_by_account(self.conn)
        bunq = next(r for r in rows if r["account"] == "(EUR) Bunq")
        self.assertTrue(bunq["is_true_balance"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_cashflow -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finance.analysis'`

- [ ] **Step 3: Write the implementation**

Create `finance/analysis/__init__.py`:

```python
"""Analyses over the warehouse views."""

from __future__ import annotations
```

Create `finance/analysis/cashflow.py`:

```python
"""Monthly income, expense, and savings rate.

Two distinctions carry the whole analysis. Interest (`проценты`) is passive
income and is reported apart from earned income, because a savings rate
inflated by it describes the deposit rather than the behaviour. And balances
are not in the export: a cumulative sum is net flow since the first imported
month, so it is only called a balance when `accounts.toml` supplies an opening
figure to anchor it.
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
    """Per-account EUR net flow, flagged as a real balance only when anchored."""
    rows = conn.execute(
        """
        SELECT a.name AS account, a.kind AS kind,
               a.opening_balance_minor AS opening,
               COALESCE((
                 SELECT SUM(COALESCE(t.income_eur_minor, 0))
                   FROM transactions t
                  WHERE t.income_account_id = a.id AND t.deleted_at IS NULL
               ), 0) -
               COALESCE((
                 SELECT SUM(COALESCE(t.outcome_eur_minor, 0))
                   FROM transactions t
                  WHERE t.outcome_account_id = a.id AND t.deleted_at IS NULL
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
            "is_true_balance": row["opening"] is not None,
        }
        for row in rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_cashflow -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add finance/analysis finance/tests/test_cashflow.py
git commit -m "feat(finance): cashflow analysis with passive-income split"
```

---

### Task 10: Category trend and drift analysis

**Files:**
- Create: `finance/analysis/categories.py`
- Test: `finance/tests/test_categories.py`

**Interfaces:**
- Consumes: view `v_monthly`
- Produces:
  - `finance.analysis.categories.matrix(conn, since: str | None = None) -> tuple[list[str], dict[str, dict[str, float]]]` — `(months, {category: {month: eur}})`
  - `finance.analysis.categories.Drift` — frozen dataclass `category: str`, `recent_mean, baseline_mean, change_ratio: float`
  - `finance.analysis.categories.drift(conn, recent: int = 6, baseline: int = 12, min_eur: float = 20.0) -> list[Drift]` — sorted by `change_ratio` descending
  - `finance.analysis.categories.year_over_year(conn) -> dict[str, dict[str, float]]` — `{category: {year: eur}}`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_categories.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import db
from finance.analysis import categories


class CategoryAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        self.conn.execute(
            "INSERT INTO categories (id, full_name, parent, leaf) "
            "VALUES (1,'Еда / Кафе и рестораны','Еда','Кафе и рестораны'),"
            "       (2,'Машина',NULL,'Машина')"
        )
        self.conn.execute("INSERT INTO accounts (id, name, kind) VALUES (1,'B','spending')")
        self._seed()
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, month: str, category_id: int, eur: float) -> None:
        minor = int(round(eur * 100))
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, category_id, kind, outcome_account_id,
               outcome_minor, outcome_currency, outcome_eur_minor, fx_source)
            VALUES (?,?,?,'outcome',1,?,'EUR',?, 'base')
            """,
            (f"{month}-{category_id}-{eur}", f"{month}-15", category_id, minor, minor),
        )

    def _seed(self) -> None:
        # 12 baseline months at 100, then 6 recent months at 200 for category 1.
        months = [f"2025-{m:02d}" for m in range(1, 13)] + [
            f"2026-{m:02d}" for m in range(1, 7)
        ]
        for index, month in enumerate(months):
            self._add(month, 1, 200.0 if index >= 12 else 100.0)
            self._add(month, 2, 50.0)

    def test_matrix_has_a_column_per_month(self) -> None:
        months, data = categories.matrix(self.conn)
        self.assertEqual(18, len(months))
        self.assertAlmostEqual(100.0, data["Еда / Кафе и рестораны"]["2025-01"])
        self.assertAlmostEqual(200.0, data["Еда / Кафе и рестораны"]["2026-06"])

    def test_drift_detects_the_doubled_category(self) -> None:
        results = categories.drift(self.conn)
        top = results[0]
        self.assertEqual("Еда / Кафе и рестораны", top.category)
        self.assertAlmostEqual(200.0, top.recent_mean, places=2)
        self.assertAlmostEqual(100.0, top.baseline_mean, places=2)
        self.assertAlmostEqual(1.0, top.change_ratio, places=6)

    def test_drift_ignores_the_flat_category(self) -> None:
        names = [d.category for d in categories.drift(self.conn)]
        self.assertNotIn("Машина", names)

    def test_drift_ignores_categories_below_the_floor(self) -> None:
        self.assertEqual([], categories.drift(self.conn, min_eur=10_000.0))

    def test_year_over_year_totals(self) -> None:
        totals = categories.year_over_year(self.conn)
        self.assertAlmostEqual(1200.0, totals["Еда / Кафе и рестораны"]["2025"])
        self.assertAlmostEqual(1200.0, totals["Еда / Кафе и рестораны"]["2026"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_categories -v`
Expected: FAIL — `ImportError: cannot import name 'categories'`

- [ ] **Step 3: Write the implementation**

Create `finance/analysis/categories.py`:

```python
"""Category spending over time, and which categories are quietly growing.

A one-off spike and a sustained shift look identical in a single month's
total. `drift` separates them by comparing a recent window's mean against the
preceding baseline window, so a category that has genuinely moved to a new
level rises to the top while a single large purchase does not.

`min_eur` keeps trivia out of the ranking: a category averaging €3 that doubles
is a 100% rise and entirely uninteresting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

DEFAULT_RECENT_MONTHS = 6
DEFAULT_BASELINE_MONTHS = 12
DEFAULT_MIN_EUR = 20.0


@dataclass(frozen=True)
class Drift:
    category: str
    recent_mean: float
    baseline_mean: float
    change_ratio: float


def matrix(
    conn: sqlite3.Connection, since: str | None = None
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Return `(months, {category: {month: eur}})`, months ascending."""
    query = "SELECT month, category, SUM(spend_eur) AS total FROM v_monthly"
    params: tuple[str, ...] = ()
    if since:
        query += " WHERE month >= ?"
        params = (since,)
    query += " GROUP BY month, category"

    data: dict[str, dict[str, float]] = {}
    months: set[str] = set()
    for row in conn.execute(query, params):
        months.add(row["month"])
        data.setdefault(row["category"], {})[row["month"]] = row["total"]
    return sorted(months), data


def drift(
    conn: sqlite3.Connection,
    recent: int = DEFAULT_RECENT_MONTHS,
    baseline: int = DEFAULT_BASELINE_MONTHS,
    min_eur: float = DEFAULT_MIN_EUR,
) -> list[Drift]:
    """Categories whose recent mean has risen above their baseline mean."""
    months, data = matrix(conn)
    if len(months) < recent + 1:
        return []

    recent_months = months[-recent:]
    baseline_months = months[-(recent + baseline) : -recent]
    if not baseline_months:
        return []

    results: list[Drift] = []
    for category, by_month in data.items():
        recent_mean = sum(by_month.get(m, 0.0) for m in recent_months) / len(
            recent_months
        )
        baseline_mean = sum(by_month.get(m, 0.0) for m in baseline_months) / len(
            baseline_months
        )
        if max(recent_mean, baseline_mean) < min_eur or baseline_mean <= 0:
            continue
        ratio = (recent_mean - baseline_mean) / baseline_mean
        if ratio <= 0:
            continue
        results.append(Drift(category, recent_mean, baseline_mean, ratio))

    results.sort(key=lambda d: d.change_ratio, reverse=True)
    return results


def year_over_year(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Yearly EUR totals per category."""
    result: dict[str, dict[str, float]] = {}
    for row in conn.execute(
        "SELECT substr(month, 1, 4) AS year, category, SUM(spend_eur) AS total "
        "FROM v_monthly GROUP BY year, category"
    ):
        result.setdefault(row["category"], {})[row["year"]] = row["total"]
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_categories -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add finance/analysis/categories.py finance/tests/test_categories.py
git commit -m "feat(finance): category trend matrix and drift detection"
```

---

### Task 11: Recurring spend detection

**Files:**
- Create: `finance/analysis/recurring.py`
- Test: `finance/tests/test_recurring.py`

**Interfaces:**
- Consumes: view `v_spend`
- Produces:
  - `finance.analysis.recurring.Cluster` — frozen dataclass `label, category, account: str`, `amount_eur: float`, `period_days: int`, `occurrences: int`, `first_date, last_date: str`, `status: str`, `monthly_eur: float`
  - `finance.analysis.recurring.detect(conn, since: str | None = None, tolerance: float = 0.05) -> list[Cluster]`
  - `finance.analysis.recurring.PERIODS: dict[str, int]`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_recurring.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from finance import db
from finance.analysis import recurring


class RecurringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        self.conn.execute(
            "INSERT INTO categories (id, full_name, parent, leaf) "
            "VALUES (1,'Отдых и развлечения / Подписки','Отдых и развлечения','Подписки')"
        )
        self.conn.execute(
            "INSERT INTO accounts (id, name, kind) VALUES (1,'(EUR) Bunq','spending')"
        )
        self.counter = 0

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, day: str, eur: float, payee: str = "") -> None:
        self.counter += 1
        minor = int(round(eur * 100))
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, category_id, payee, kind, outcome_account_id,
               outcome_minor, outcome_currency, outcome_eur_minor, fx_source)
            VALUES (?,?,1,?,'outcome',1,?,'EUR',?,'base')
            """,
            (str(self.counter), day, payee, minor, minor),
        )

    def _monthly_series(self, start: str, count: int, eur: float, payee="") -> None:
        first = date.fromisoformat(start)
        for index in range(count):
            self._add((first + timedelta(days=30 * index)).isoformat(), eur, payee)
        self.conn.commit()

    def test_detects_a_monthly_subscription(self) -> None:
        self._monthly_series("2026-01-05", 8, 15.99, "Zoom")
        clusters = recurring.detect(self.conn)
        self.assertEqual(1, len(clusters))
        self.assertEqual(30, clusters[0].period_days)
        self.assertEqual(8, clusters[0].occurrences)

    def test_uses_payee_as_the_label_when_present(self) -> None:
        self._monthly_series("2026-01-05", 8, 15.99, "Zoom")
        self.assertEqual("Zoom", recurring.detect(self.conn)[0].label)

    def test_falls_back_to_category_and_amount_without_a_payee(self) -> None:
        self._monthly_series("2026-01-05", 8, 15.99)
        label = recurring.detect(self.conn)[0].label
        self.assertIn("Подписки", label)
        self.assertIn("15.99", label)

    def test_ignores_irregular_spending(self) -> None:
        for day, eur in (
            ("2026-01-03", 4.20),
            ("2026-01-04", 51.00),
            ("2026-02-19", 9.10),
            ("2026-05-02", 33.00),
        ):
            self._add(day, eur)
        self.conn.commit()
        self.assertEqual([], recurring.detect(self.conn))

    def test_requires_a_minimum_number_of_occurrences(self) -> None:
        self._monthly_series("2026-01-05", 2, 15.99, "Zoom")
        self.assertEqual([], recurring.detect(self.conn))

    def test_amounts_within_tolerance_join_one_cluster(self) -> None:
        first = date.fromisoformat("2026-01-05")
        for index in range(6):
            self._add(
                (first + timedelta(days=30 * index)).isoformat(),
                15.99 if index % 2 else 16.20,
                "Zoom",
            )
        self.conn.commit()
        self.assertEqual(1, len(recurring.detect(self.conn)))

    def test_monthly_load_normalises_an_annual_charge(self) -> None:
        for year in range(2020, 2026):
            self._add(f"{year}-03-05", 120.0, "Domain")
        self.conn.commit()
        cluster = recurring.detect(self.conn)[0]
        self.assertAlmostEqual(10.0, cluster.monthly_eur, places=1)

    def test_dormant_when_the_series_stopped(self) -> None:
        self._monthly_series("2020-01-05", 8, 15.99, "Old")
        self.assertEqual("dormant", recurring.detect(self.conn)[0].status)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_recurring -v`
Expected: FAIL — `ImportError: cannot import name 'recurring'`

- [ ] **Step 3: Write the implementation**

Create `finance/analysis/recurring.py`:

```python
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

    latest = conn.execute(
        "SELECT MAX(date) AS d FROM v_spend"
    ).fetchone()["d"]
    if latest is None:
        return []
    latest_date = date.fromisoformat(latest)

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
            silent_days = (latest_date - days[-1]).days
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_recurring -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Sanity-check against the real database**

```bash
python3 -c "
from finance import db
from finance.analysis import recurring
conn = db.connect()
for c in recurring.detect(conn, since='2024-09')[:15]:
    print(f'{c.monthly_eur:8.2f}/mo  {c.status:15s} {c.label[:45]}')
"
```

Expected: recognisable subscriptions near the top. If the list is dominated by noise, raise `MIN_OCCURRENCES` or tighten `GAP_VARIATION_LIMIT` and re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add finance/analysis/recurring.py finance/tests/test_recurring.py
git commit -m "feat(finance): payee-independent recurring spend detection"
```

---

### Task 12: Budget baselines and anomaly detection

**Files:**
- Create: `finance/analysis/budget.py`
- Test: `finance/tests/test_budget.py`

**Interfaces:**
- Consumes: views `v_monthly`, `v_spend`
- Produces:
  - `finance.analysis.budget.Baseline` — frozen dataclass `category: str`, `budget_eur, actual_eur, variance_eur, variance_ratio: float`
  - `finance.analysis.budget.baselines(conn, month: str, window: int = 12, trim: float = 0.1) -> list[Baseline]`
  - `finance.analysis.budget.Outlier` — frozen dataclass `date, category, payee: str`, `amount_eur, threshold_eur: float`
  - `finance.analysis.budget.outliers(conn, since: str | None = None, percentile: float = 0.95) -> list[Outlier]`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_budget.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance import db
from finance.analysis import budget


class BudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        self.conn.execute(
            "INSERT INTO categories (id, full_name, parent, leaf) "
            "VALUES (1,'Еда / Продукты','Еда','Продукты')"
        )
        self.conn.execute(
            "INSERT INTO accounts (id, name, kind) VALUES (1,'B','spending')"
        )
        self.counter = 0

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, day: str, eur: float, payee: str = "") -> None:
        self.counter += 1
        minor = int(round(eur * 100))
        self.conn.execute(
            """
            INSERT INTO transactions
              (id, date, category_id, payee, kind, outcome_account_id,
               outcome_minor, outcome_currency, outcome_eur_minor, fx_source)
            VALUES (?,?,1,?,'outcome',1,?,'EUR',?,'base')
            """,
            (str(self.counter), day, payee, minor, minor),
        )

    def test_budget_is_the_trimmed_median_of_history(self) -> None:
        for month in range(1, 13):
            self._add(f"2025-{month:02d}-10", 100.0)
        self._add("2026-01-10", 300.0)
        self.conn.commit()
        result = budget.baselines(self.conn, "2026-01")
        row = next(r for r in result if r.category == "Еда / Продукты")
        self.assertAlmostEqual(100.0, row.budget_eur, places=2)
        self.assertAlmostEqual(300.0, row.actual_eur, places=2)
        self.assertAlmostEqual(200.0, row.variance_eur, places=2)
        self.assertAlmostEqual(2.0, row.variance_ratio, places=6)

    def test_a_single_spike_does_not_move_the_budget(self) -> None:
        for month in range(1, 12):
            self._add(f"2025-{month:02d}-10", 100.0)
        self._add("2025-12-10", 5000.0)
        self._add("2026-01-10", 100.0)
        self.conn.commit()
        row = budget.baselines(self.conn, "2026-01")[0]
        self.assertLess(row.budget_eur, 200.0, "trimming must discard the spike")

    def test_months_without_history_are_skipped(self) -> None:
        self._add("2026-01-10", 100.0)
        self.conn.commit()
        self.assertEqual([], budget.baselines(self.conn, "2026-01"))

    def test_outliers_flag_the_unusual_transaction(self) -> None:
        for day in range(1, 21):
            self._add(f"2026-01-{day:02d}", 10.0)
        self._add("2026-01-25", 500.0, "Big Shop")
        self.conn.commit()
        found = budget.outliers(self.conn)
        self.assertEqual(1, len(found))
        self.assertEqual("Big Shop", found[0].payee)
        self.assertAlmostEqual(500.0, found[0].amount_eur, places=2)

    def test_no_outliers_when_spending_is_uniform(self) -> None:
        for day in range(1, 21):
            self._add(f"2026-01-{day:02d}", 10.0)
        self.conn.commit()
        self.assertEqual([], budget.outliers(self.conn))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_budget -v`
Expected: FAIL — `ImportError: cannot import name 'budget'`

- [ ] **Step 3: Write the implementation**

Create `finance/analysis/budget.py`:

```python
"""Budgets derived from actual history, and transactions that break the pattern.

An invented target tells you nothing you did not already believe. A budget
taken from what the owner actually spends is falsifiable, so the baseline here
is the trimmed median of the trailing window: discard the extreme months at
both ends, then take the middle. One holiday does not raise the grocery budget
for the rest of the year.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass

DEFAULT_WINDOW_MONTHS = 12
DEFAULT_TRIM = 0.1
DEFAULT_PERCENTILE = 0.95
MIN_HISTORY_MONTHS = 3
MIN_OUTLIER_SAMPLES = 8


@dataclass(frozen=True)
class Baseline:
    category: str
    budget_eur: float
    actual_eur: float
    variance_eur: float
    variance_ratio: float


@dataclass(frozen=True)
class Outlier:
    date: str
    category: str
    payee: str
    amount_eur: float
    threshold_eur: float


def _trimmed_median(values: list[float], trim: float) -> float:
    ordered = sorted(values)
    cut = int(len(ordered) * trim)
    kept = ordered[cut : len(ordered) - cut] or ordered
    return statistics.median(kept)


def baselines(
    conn: sqlite3.Connection,
    month: str,
    window: int = DEFAULT_WINDOW_MONTHS,
    trim: float = DEFAULT_TRIM,
) -> list[Baseline]:
    """Compare `month` against a trimmed median of the preceding `window`."""
    history: dict[str, dict[str, float]] = {}
    for row in conn.execute(
        "SELECT month, category, SUM(spend_eur) AS total FROM v_monthly "
        "WHERE month < ? GROUP BY month, category",
        (month,),
    ):
        history.setdefault(row["category"], {})[row["month"]] = row["total"]

    actuals = {
        row["category"]: row["total"]
        for row in conn.execute(
            "SELECT category, SUM(spend_eur) AS total FROM v_monthly "
            "WHERE month = ? GROUP BY category",
            (month,),
        )
    }

    results: list[Baseline] = []
    for category, by_month in history.items():
        recent = [by_month[m] for m in sorted(by_month)[-window:]]
        if len(recent) < MIN_HISTORY_MONTHS:
            continue
        budget_eur = _trimmed_median(recent, trim)
        actual = actuals.get(category, 0.0)
        variance = actual - budget_eur
        results.append(
            Baseline(
                category=category,
                budget_eur=budget_eur,
                actual_eur=actual,
                variance_eur=variance,
                variance_ratio=(variance / budget_eur) if budget_eur else 0.0,
            )
        )

    results.sort(key=lambda b: b.variance_eur, reverse=True)
    return results


def outliers(
    conn: sqlite3.Connection,
    since: str | None = None,
    percentile: float = DEFAULT_PERCENTILE,
) -> list[Outlier]:
    """Transactions above their own category's percentile threshold."""
    query = (
        "SELECT date, month, COALESCE(category, '(uncategorised)') AS category, "
        "payee, outcome_eur FROM v_spend WHERE outcome_eur IS NOT NULL"
    )
    params: tuple[str, ...] = ()
    if since:
        query += " AND month >= ?"
        params = (since,)

    by_category: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(query, params):
        by_category.setdefault(row["category"], []).append(row)

    found: list[Outlier] = []
    for category, rows in by_category.items():
        if len(rows) < MIN_OUTLIER_SAMPLES:
            continue
        amounts = sorted(r["outcome_eur"] for r in rows)
        index = min(int(len(amounts) * percentile), len(amounts) - 1)
        threshold = amounts[index]
        for row in rows:
            if row["outcome_eur"] > threshold:
                found.append(
                    Outlier(
                        date=row["date"],
                        category=category,
                        payee=row["payee"],
                        amount_eur=row["outcome_eur"],
                        threshold_eur=threshold,
                    )
                )

    found.sort(key=lambda o: o.amount_eur, reverse=True)
    return found
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_budget -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add finance/analysis/budget.py finance/tests/test_budget.py
git commit -m "feat(finance): history-derived budgets and per-category outliers"
```

---

### Task 13: Advisory report composition

**Files:**
- Create: `finance/report.py`
- Modify: `finance/cli.py`
- Test: `finance/tests/test_report.py`

**Interfaces:**
- Consumes: `verify`, all four analysis modules
- Produces:
  - `finance.report.build(conn, months: int = 24) -> dict` — the full analysis payload, JSON-serialisable
  - `finance.report.to_markdown(payload: dict) -> str`
  - `finance.report.REPORT_DIR: Path` = `Path("reports")`

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_report.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance import accounts, db, fx, ingest, report

FIXTURES = Path(__file__).parent / "fixtures"


class ReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.migrate(self.conn)
        ingest.ingest_file(self.conn, FIXTURES / "full_dialect.csv")
        accounts.apply_toml(self.conn, accounts.seed_toml(self.conn))
        fx.store_rates(
            self.conn,
            {"2026-07-03": {"RUB": 0.01}, "2026-07-04": {"RUB": 0.01, "USD": 0.9}},
            "ecb",
        )
        fx.materialise(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_payload_is_json_serialisable(self) -> None:
        json.dumps(report.build(self.conn))

    def test_payload_carries_every_section(self) -> None:
        payload = report.build(self.conn)
        for key in (
            "coverage",
            "fx_precision",
            "cashflow",
            "drift",
            "recurring",
            "budget",
            "outliers",
        ):
            self.assertIn(key, payload)

    def test_coverage_cutoff_is_the_real_last_month(self) -> None:
        self.assertEqual("2026-07", report.build(self.conn)["coverage"]["last_month"])

    def test_markdown_states_the_cutoff(self) -> None:
        text = report.to_markdown(report.build(self.conn))
        self.assertIn("2026-07", text)

    def test_markdown_discloses_fx_precision(self) -> None:
        text = report.to_markdown(report.build(self.conn))
        self.assertIn("exchange rate", text.lower())

    def test_empty_database_produces_a_report_not_a_crash(self) -> None:
        self.conn.execute("DELETE FROM transactions")
        self.conn.commit()
        text = report.to_markdown(report.build(self.conn))
        self.assertIn("no transactions", text.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest finance.tests.test_report -v`
Expected: FAIL — `ImportError: cannot import name 'report'`

- [ ] **Step 3: Write the implementation**

Create `finance/report.py`:

```python
"""Assemble the analyses into one payload and render it as markdown.

`build` produces data and nothing else — no prose, no judgement — so the same
payload feeds the markdown report, the Artifact dashboard, and the advisory
skill's own reading of the numbers. Interpretation belongs to the skill; this
module's only opinion is that a report must state its cutoff and disclose how
much of its EUR total rests on inferred exchange rates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

from . import verify
from .analysis import budget, cashflow, categories, recurring

REPORT_DIR = Path("reports")
DEFAULT_MONTHS = 24
TOP_N = 12
INFERRED_SOURCES = ("implied", "filled", "unresolved")


def _since(conn: sqlite3.Connection, months: int) -> str | None:
    row = conn.execute(
        "SELECT MAX(substr(date, 1, 7)) AS m FROM transactions WHERE deleted_at IS NULL"
    ).fetchone()
    if row is None or row["m"] is None:
        return None
    year, month = int(row["m"][:4]), int(row["m"][5:])
    total = year * 12 + (month - 1) - (months - 1)
    return f"{total // 12}-{total % 12 + 1:02d}"


def build(conn: sqlite3.Connection, months: int = DEFAULT_MONTHS) -> dict:
    """Every number the advisory needs, as plain JSON-safe structures."""
    coverage = verify.coverage(conn)
    since = _since(conn, months)
    if since is None:
        return {
            "coverage": asdict(coverage),
            "fx_precision": {},
            "cashflow": [],
            "drift": [],
            "recurring": [],
            "budget": [],
            "outliers": [],
            "net_flow": [],
            "window_months": months,
            "since": None,
        }

    rows = cashflow.monthly(conn, since=since)
    return {
        "coverage": asdict(coverage),
        "fx_precision": verify.fx_precision(conn),
        "cashflow": [asdict(row) for row in rows],
        "cashflow_3m": cashflow.trailing_mean(rows, "spend_eur", 3),
        "cashflow_12m": cashflow.trailing_mean(rows, "spend_eur", 12),
        "net_flow": cashflow.net_flow_by_account(conn),
        "drift": [asdict(d) for d in categories.drift(conn)[:TOP_N]],
        "year_over_year": categories.year_over_year(conn),
        "recurring": [asdict(c) for c in recurring.detect(conn, since=since)[:TOP_N]],
        "budget": [
            asdict(b)
            for b in budget.baselines(conn, coverage.last_month or "")[:TOP_N]
        ],
        "outliers": [asdict(o) for o in budget.outliers(conn, since=since)[:TOP_N]],
        "window_months": months,
        "since": since,
    }


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


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
        "later months are not in the data.",
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
        f"{1 - inferred:.1%} of converted volume uses published or base rates; "
        f"{inferred:.1%} rests on inferred ones.",
        "",
        "| source | share |",
        "| --- | ---: |",
    ]
    for source, share in sorted(
        payload["fx_precision"].items(), key=lambda kv: -kv[1]
    ):
        lines.append(f"| {source} | {share:.1%} |")

    lines += [
        "",
        "## Monthly cashflow",
        "",
        "| month | earned | passive | spend | net | savings rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["cashflow"]:
        rate = (
            "—" if row["savings_rate"] is None else f"{row['savings_rate']:.0%}"
        )
        lines.append(
            f"| {row['month']} | {_fmt(row['earned_eur'])} | "
            f"{_fmt(row['passive_eur'])} | {_fmt(row['spend_eur'])} | "
            f"{_fmt(row['net_eur'])} | {rate} |"
        )

    lines += [
        "",
        "## Categories drifting upward",
        "",
        "| category | recent 6m avg | prior 12m avg | change |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in payload["drift"]:
        lines.append(
            f"| {row['category']} | {_fmt(row['recent_mean'])} | "
            f"{_fmt(row['baseline_mean'])} | +{row['change_ratio']:.0%} |"
        )

    lines += [
        "",
        "## Recurring charges",
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

    balances = [r for r in payload["net_flow"] if not r["is_true_balance"]]
    if balances:
        lines += [
            "",
            f"> {len(balances)} account(s) have no opening balance in "
            "`accounts.toml`, so their figures are net flow since the first "
            "imported month, **not balances**. Runway is omitted for them.",
        ]

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest finance.tests.test_report -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Wire the CLI**

In `finance/cli.py` add `import json` and `from . import report`, then register:

```python
    report_cmd = sub.add_parser("report", help="write the advisory report")
    report_cmd.add_argument("--months", type=int, default=report.DEFAULT_MONTHS)
    report_cmd.add_argument("--json", action="store_true")
    report_cmd.add_argument("--out", type=Path)
```

and the branch:

```python
    if args.command == "report":
        conn = db.connect(args.db)
        db.migrate(conn)
        payload = report.build(conn, months=args.months)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        text = report.to_markdown(payload)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(text)
        return 0
```

- [ ] **Step 6: Run the full suite and the real report**

```bash
python3 -m unittest discover -s finance/tests -t . -v
python3 -m finance report --months 24 | head -60
```

Expected: all tests pass; the report shows 24 months of real cashflow ending at `2026-08`.

- [ ] **Step 7: Commit**

```bash
git add finance/report.py finance/cli.py finance/tests/test_report.py
git commit -m "feat(finance): advisory report payload and markdown rendering"
```

---

### Task 14: The `/finance-advisor` skill

**Files:**
- Create: `.claude/skills/finance-advisor/SKILL.md`
- Modify: `CLAUDE.md` (document the warehouse), `README.md` if one exists

**Interfaces:**
- Consumes: the `finance` CLI
- Produces: the skill

- [ ] **Step 1: Write the skill**

Create `.claude/skills/finance-advisor/SKILL.md`:

```markdown
---
name: finance-advisor
description: Use when the user asks for financial analysis, budget advice, spending trends, savings rate, subscription review, or "how am I doing" questions about their money. Reads the SQLite finance warehouse built from ZenMoney CSV exports. Triggers — "analyse my spending", "financial advice", "budget review", "where is my money going", "какие у меня траты", "проанализируй бюджет".
---

# Financial advisor

Interpret the numbers. Do not merely restate them — the user can read a table.

## 1. Refresh and verify

```bash
python3 -m finance ingest        # picks up anything new in data/dumps/
python3 -m finance fx --refresh  # only if ingest reported new rows
python3 -m finance verify
```

Read the coverage line. **Every figure you quote must name its real cutoff
month.** If `verify` reports missing months, say so before any conclusion — a
gap in the middle of a trend invalidates the trend.

If `unresolved` is above zero in the FX output, name the affected currencies
and dates and tell the user to add them to `fx_overrides.toml`.

## 2. Pull the analysis

```bash
python3 -m finance report --months 24 --json
```

This is the whole payload: coverage, FX precision, monthly cashflow, category
drift, recurring charges, budget variance, and outliers. Read it, then reason.

For anything the payload does not answer, query directly — the views are the
contract:

```bash
python3 -m finance query "SELECT month, SUM(spend_eur) FROM v_monthly GROUP BY month ORDER BY month DESC LIMIT 12"
```

`v_spend` excludes transfers, `Корректировка`, and moves into savings and
investment accounts. `v_income` flags passive interest separately. Never
compute spending off the raw `transactions` table — you will double-count
transfers.

## 3. Write the advisory

Cover, in this order, and lead each section with the conclusion:

1. **Where the money went** — the shape of the last 24 months, not a category dump
2. **What changed** — drift, with the honest question of whether it was a choice
3. **Recurring load** — the total monthly commitment, and which entries are dormant or have quietly risen
4. **Savings rate** — the trend, and what earned income alone would give without passive interest
5. **What to do** — at most three concrete actions, each with the euro figure it frees per year

Rules that keep the advice honest:

- **Quote the FX precision** whenever a total leans on RUB or KZT.
- **Never call net flow a balance.** Accounts without an `opening_balance` in
  `accounts.toml` have unknown starting amounts; say so rather than implying
  net worth.
- **Do not invent targets.** Budgets come from the user's own trimmed history.
  "You should spend less on X" is worthless without what they actually spend.
- **Uncategorised spending is a finding**, not a rounding error. If it is
  material, report the amount and suggest categorising it.
- Payee is absent for 2020–2025. Do not claim merchant-level insight for those
  years.

## 4. Deliver

Write `reports/YYYY-MM-DD-advisory.md`:

```bash
python3 -m finance report --months 24 --out reports/$(date +%F)-advisory.md
```

Then publish an Artifact dashboard. **Load the `dataviz` skill before writing
any chart code**, and load `artifact-design` before writing the page. Charts
worth having:

- monthly spend with the 3- and 12-month trailing means
- savings rate over time, earned income separated from passive
- the drift table as a slope chart, recent 6m against prior 12m
- recurring load as a stacked monthly commitment

Charts are inline SVG. Do not add a charting library — the page must work
offline, and the CSP allowlist does not cover arbitrary CDNs.

Hand the user the Artifact link and the report path.
```

- [ ] **Step 2: Verify the skill loads**

Run: `python3 -c "
import re, pathlib
text = pathlib.Path('.claude/skills/finance-advisor/SKILL.md').read_text()
assert text.startswith('---'), 'missing frontmatter'
assert 'name: finance-advisor' in text
assert 'description:' in text
print('frontmatter ok')
"`

Expected: `frontmatter ok`

- [ ] **Step 3: Document the warehouse in CLAUDE.md**

Append a section to `CLAUDE.md`:

```markdown
## Finance warehouse (analytics)

Unrelated to submitting transactions. `finance/` loads ZenMoney CSV exports
into `data/finance.db` and analyses them. Use `/finance-advisor` for advice.

```bash
python3 -m finance ingest --from data/dumps   # idempotent; safe to re-run
python3 -m finance fx --refresh               # EUR rates + materialisation
python3 -m finance verify                     # coverage, gaps, FX precision
python3 -m finance report --months 24         # advisory tables
python3 -m finance query "SELECT ..."         # ad-hoc SQL
```

Query through the **views**, never the raw table: `v_spend` (transfers,
`Корректировка`, and savings/investment moves already excluded), `v_income`
(passive interest flagged), `v_monthly`, `v_transactions`.

Two gotchas the importer already handles, worth knowing before you touch it:
ZenMoney emits two CSV dialects whose transfer encoding is *opposite* (the
full export fills both account names on every row), and payee is empty for
2020–2025, so nothing may key on merchant name.

`accounts.toml` classifies accounts as spending/cash/savings/investment/
credit/debt — the savings rate is wrong if it is wrong. `fx_overrides.toml`
supplies rates the ECB never published (KZT, and RUB after March 2022).

Run `bun run check` (typecheck + Bun tests + Python tests) after changes.
```

- [ ] **Step 4: Run the whole suite**

Run: `bun run check`
Expected: typecheck passes, Bun tests pass, all Python tests pass

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/finance-advisor/SKILL.md CLAUDE.md
git commit -m "feat(finance): /finance-advisor skill and warehouse documentation"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: two dialects and kind
derivation → Task 2; fingerprint exclusions and ordinals → Task 3;
range-scoped reconciliation and re-ingest-is-a-no-op → Task 4; `accounts.toml`
with `alias_of` and opening balances → Tasks 5 and 9; the four FX layers and
precision disclosure → Tasks 6, 7, 8; the four views → Task 8; the four
analyses → Tasks 9–12; the advisory skill with Artifact dashboard → Tasks 13
and 14. The spec's "out of scope" list is respected — no web server, no Diff
API pull, no forecasting, no changes to `src/`.

**Known follow-ups, deliberately deferred.** The `qrCode` column is empty in
all 17,397 rows and is dropped rather than stored. Multi-file `import_batches`
records one row per file rather than per invocation. Neither affects any
analysis.

**Verification gates.** Four steps check the plan against the real exports
rather than against fixtures. All four numbers below were confirmed by
prototyping the algorithms against the real files before this plan was
written, so a disagreement is a defect in the implementation, not a surprise
in the data:

1. Task 2 Step 6 → `17397 Counter({'outcome': 13675, 'transfer': 2381, 'income': 1341})`
2. Task 3 Step 5 → `17397 17397`, `order-independent: True`, and
   `ids not in full dump: 0`
3. Task 4 Step 6 → a second ingest run reporting `new=0 updated=0 deleted=0`
4. Task 8 Step 6 → `months: 155 (2013-10 → 2026-08)`, `missing: none`

**Two normalisations exist only because prototyping found them**, and removing
either silently duplicates rows:

- `normalise_text` collapses whitespace runs. One comment is spaced
  differently in the two exports (`Циан.  Занесены` vs `Циан. Занесены`),
  which alone caused 1 phantom row.
- `account_currency` drops the currency for accounts whose name does not
  declare one. ZenMoney stamps `Debts` as EUR in the full export and RUB in
  the per-month exports, which alone caused 79 phantom rows.

Their tests in Task 2 are regression tests, not decoration.
