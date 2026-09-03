# Personal Finance Warehouse & Advisory Tooling — Design

Date: 2026-09-03
Status: approved for planning

## Problem

Thirteen years of ZenMoney history exist only as CSV dumps in
`~/Downloads`. They cannot be queried, cross-referenced, or trended. The
owner wants a SQLite database he can point his own reporting at, a set
of analyses that explain the last two years, and an advisory skill that
interprets the numbers rather than restating them. The importer must
accept further dumps without rework.

## Source data — verified facts

Verified on 2026-09-03 against the actual files.

### Primary source: the full dump

`zen_2026-09-03_dumpof_transactions_1788441684.csv` — one file, every
transaction.

| Property | Value |
| --- | --- |
| Rows | 17,397 |
| Coverage | 2013-10-13 → 2026-08-31 |
| Months | 155 of 155 in range — **no gaps** |
| Currencies (per row) | RUB 11,509 · EUR 5,038 · USD 806 · KZT 44 |
| Accounts | 48 |
| Categories | 108 distinct labels |
| Row kinds | outcome 13,675 · income 1,341 · transfer 2,381 |
| Uncategorised | 2,819 |
| Cross-currency transfers | 666 |

**ZenMoney holds no data before 2013-10-13.** The stated goal of
importing from 2010 is already satisfied as far as the source allows;
there is nothing earlier to import.

This file supersedes the 67 per-month dumps: it contains exactly 8,624
rows in 2021-01…2026-07, matching the monthly set's total precisely, and
extends the history back a further seven years plus forward through
2026-08.

### Two CSV dialects

The importer must sniff the dialect; they differ in five ways.

| | Full dump | Per-month dump |
| --- | --- | --- |
| Delimiter | `;` | `,` |
| Preamble | none — header is line 1 | two junk lines before the header |
| Decimals | dot (`0.95`) | comma, spaces as thousands (`"2 106,00"`) |
| Extra column | `qrCode` (empty in all 17,397 rows) | absent |
| Unused transfer side | same account repeated, amount `0` | account name empty |

Both begin with a UTF-8 BOM and share the same twelve core columns:

```
date, categoryName, payee, comment,
outcomeAccountName, outcome, outcomeCurrencyShortTitle,
incomeAccountName,  income,  incomeCurrencyShortTitle,
createdDate, changedDate
```

### Kind derivation is dialect-dependent

This is the subtlest difference and the one most likely to cause a
silent, total misclassification.

In the **per-month** dialect an expense leaves `incomeAccountName`
empty, so presence-of-both-account-names identifies a transfer. In the
**full** dialect *both account names are always populated* — all 17,397
rows — with a `0` amount on the unused side. Applying the per-month rule
to the full dump classifies **every row as a transfer**, which silently
empties all spending analysis.

The rule must therefore key on amounts, not on name presence:

```
outcome > 0 and income > 0  → transfer
outcome > 0                 → outcome
income  > 0                 → income
```

Verified against the full dump: this yields 13,675 / 1,341 / 2,381 with
zero same-account transfers and zero rows where both sides are `0`.

### Other facts the implementation must respect

1. **There is no transaction id.** Dedup must be derived from content.
2. **Payee coverage is U-shaped**, so no analysis may depend on it:
   2013 16% · 2014 40% · 2015 45% · 2016 58% · 2017 65% · 2018 59% ·
   2019 52% · **2020–2025 0.4–3.4%** · 2026 73%.
3. **Category labels are hierarchical but the separator is `" / "`, not
   `"/"`.** Four of the 108 labels contain a bare slash inside the child
   name (e.g. `Отпуск / 2023 France/Switzeland`). Split on `" / "` with
   `maxsplit=1`. Distribution: 30 top-level, 74 two-level, 4 with an
   embedded slash.
4. **`Корректировка` (261 rows) is a balance correction, not spending**,
   and must be excluded from expense totals.
5. **`проценты` (732 rows) is deposit interest** — passive income,
   reported separately from earned income.
6. Account names are dirty and near-duplicated (`Карточка - Альфа` vs
   `(RUB) Карточка Альфа`; eight variants of `Тинькофф депозит`).
   `accounts.toml` carries an optional `alias_of` so these can be merged
   without editing data.

## FX — the hard constraint

Every cross-currency total needs a EUR conversion at the transaction's
own date. Coverage from `api.frankfurter.dev` (ECB, free, no key, bulk
timeseries endpoint) was tested directly:

| Currency | ECB coverage | Gap |
| --- | --- | --- |
| EUR | base | — |
| USD | full range | none |
| RUB | through 2022-03 only (ECB delisted RUB) | 54 months |
| KZT | never published | all |

The gap is far smaller than it first appears:

- **85% of RUB rows (9,838 of 11,509) predate the delisting** and are
  ECB-exact. The gap era is 1,671 rows — **9.6% of the dataset**.
- 666 cross-currency transfers are observations of the rate the owner
  actually realised. 178 of them fall in the RUB gap era, covering
  **43 of its 54 months**.
- KZT is negligible: 44 rows total, 6 in the last two years.
- **In the last 24 months, 95% of rows are EUR or USD**, both
  ECB-exact. The two-year analysis rests almost entirely on hard rates.

### Resolver

Four layers, each recorded in `fx_rates.source` so precision is never
silently lost:

1. `ecb` — fetched once per currency-range, cached
2. `implied` — derived from cross-currency transfers
3. `manual` — `fx_overrides.toml`, hand-maintained
4. `filled` — linear interpolation between known points, forward-fill
   past the last known point

**Every report must state what share of its EUR total rests on
`implied` or `filled` rates.** Disclosing the uncertainty is a
requirement, not a nicety.

## Architecture

```
finance/                     # new Python package, committed
  __init__.py
  schema.sql                 # DDL and views
  db.py                      # connection, migration runner
  dialects.py                # CSV sniffing and row normalisation
  ingest.py                  # folder scan → normalise → upsert
  fx.py                      # layered rate resolver
  report.py                  # advisory composition
  cli.py                     # ingest | fx | verify | report | query
  analysis/
    cashflow.py
    categories.py
    recurring.py
    budget.py
  tests/
    fixtures/full_dialect.csv
    fixtures/month_dialect.csv
accounts.toml                # account → kind and alias_of; committed
fx_overrides.toml            # manual FX rates; committed
data/finance.db              # gitignored (data/ already is)
data/dumps/                  # default ingest folder, gitignored
```

`src/` (Bun/TypeScript, the ZenMoney submit path) is untouched. Python
was chosen for the analytics half, following the precedent already set
by `scripts/detect_zenmoney_log.py`.

### Schema

One row per transaction with both legs inline, mirroring the CSV. A
double-entry postings ledger was considered and rejected: it doubles row
count and complicates dedup to buy rigour that ZenMoney's own balance
reconciliation already provides.

```
accounts(id, name, currency, kind, alias_of, is_active)
categories(id, full_name, parent, leaf)
transactions(
  id TEXT PRIMARY KEY,        -- fingerprint, see below
  date, category_id,
  payee, comment,
  outcome_account_id, outcome, outcome_currency,
  income_account_id,  income,  income_currency,
  kind,                       -- outcome | income | transfer
  outcome_eur, income_eur,    -- materialised, refreshed when rates change
  created_at, changed_at,
  source_file, imported_at, deleted_at
)
fx_rates(date, currency, eur_per_unit, source)
import_batches(id, ran_at, files, rows_seen, rows_new, rows_updated, rows_deleted)
```

`accounts.kind` vocabulary: `spending | cash | savings | investment |
credit | debt`. This is what makes the savings rate correct — money
moved to `(RUB) Тинькофф Накопительный` is saving, not spending, and
`(RUB) Тинькофф кредитка` is a liability rather than an asset. The file
is seeded by heuristic (`накопительный`/`депозит`/`вклад` → savings;
`Брокерский счет`/`ИИС`/`Interactive brokers` → investment; `Debts` →
debt; `cash`/`Наличные`/`кубышка` → cash; `кредитка` → credit; rest →
spending) and then corrected by hand.

### Views — the reporting contract

Views exist so the owner's own SQL never has to re-derive the exclusion
rules:

- `v_transactions` — all live rows, enriched with EUR amounts, parent
  and leaf category, and account kind
- `v_spend` — outcomes only; transfers, `Корректировка`, and moves into
  savings/investment excluded
- `v_income` — income, with passive interest (`проценты`) flagged
  separately from earned income
- `v_monthly` — month × category × EUR, ready to pivot

### Idempotent ingest

Fingerprint over **identity fields only**, computed on normalised values
so the two dialects produce identical hashes for the same transaction:

```
sha256(date | payee | comment |
       outcome_account | outcome | outcome_currency |
       income_account  | income  | income_currency)
```

`changedDate` is excluded — it shifts on every re-export. **Category is
excluded too**, because it is mutable: when a transaction is
recategorised in ZenMoney and re-exported, the row must be *updated in
place*, not duplicated.

Normalisation before hashing is what makes cross-dialect dedup work:
decimal separator unified, thousands separators stripped, and the full
dialect's `0`-amount phantom side reduced to the per-month dialect's
empty side.

Genuinely identical transactions on one day (two €4.20 coffees) collide.
They are disambiguated by a deterministic occurrence ordinal appended
within the identical-field group, ordered by `createdDate`. Both rows
survive; the assignment is stable across re-exports.

**Range-scoped reconciliation.** A dump covers a date range — one month
for the per-month dialect, the whole history for the full dump. After
ingesting a file, live rows whose date falls inside that file's observed
range but whose fingerprint is absent from it are soft-deleted
(`deleted_at` set), so a transaction deleted in ZenMoney disappears from
reports without destroying the audit trail. Scoping to the observed
range rather than to a hardcoded month is what lets both dialects share
one code path.

Re-running ingest over an unchanged folder must produce zero new, zero
updated, and zero deleted rows. This is a test, not an aspiration.

`finance verify` reports covered months, names any gap, and flags absent
recent months.

## Analyses

### 1. cashflow

Monthly earned income, passive income, true expense, net, and savings
rate. Trailing 3-month and 12-month means.

**Balances are not in the source data and must not be faked.** The dumps
contain transactions only, so a cumulative sum per account is *net flow
since the first imported month*, not a balance — accounts opened before
2013-10 carry an unknown opening amount. Therefore:

- `accounts.toml` accepts optional `opening_balance` and `opening_date`
  per account. Where both are supplied, the derived balance is a true
  balance and is labelled as such.
- Where they are absent, the figure is reported as **net flow** with an
  explicit "not a balance" label, and never presented as net worth.
- Runway is computed only from accounts with known opening balances, and
  the report states how many accounts were excluded for lack of one. If
  too few are known to be meaningful, runway is omitted rather than
  guessed.

### 2. categories

Month × category EUR matrix. 3- and 12-month moving averages, YoY
deltas, and **drift detection**: categories whose trailing 6-month mean
has risen materially above their prior 12-month mean, which surfaces
quiet creep rather than one-off spikes.

### 3. recurring

Because payee is unavailable for 2020–2025, recurrence is detected on
**(category, account, amount cluster)** signatures: group rows, cluster
amounts within a tolerance, and accept a cluster as recurring when the
median inter-occurrence gap matches a weekly/monthly/quarterly/annual
period with low variance. Payee, when present, is used only to *name*
the cluster. Output: total monthly recurring load, and clusters flagged
**new**, **price-increased**, or **dormant**.

### 4. budget

Budgets are derived from the owner's own trimmed history — the median of
the trailing 12 months per category, discarding the top and bottom
decile — rather than invented targets. Reports monthly variance against
that baseline and per-transaction outliers above the category's p95.

## Advisor skill

`.claude/skills/finance-advisor/SKILL.md`, invocable as
`/finance-advisor`. Sequence:

1. `finance verify` — establish coverage. Gaps do not block the
   advisory; they are stated in it, and every period figure names its
   actual cutoff date rather than implying the data runs to today
2. run the four analyses
3. write an interpretation: what changed, what is drifting, what a given
   cut is worth in months of runway, where the FX uncertainty sits
4. emit `reports/YYYY-MM-DD-advisory.md` and publish an Artifact
   dashboard with the category trend, savings-rate, and recurring-load
   charts

The skill interprets; it does not merely tabulate.

## Testing

Test-driven, per the project's existing practice.

- Dialect fixtures for both formats, each exercising every row kind,
  both decimal formats, the `" / "` category split with an embedded
  slash, and a same-day duplicate pair
- **Cross-dialect identity**: the same transaction expressed in both
  dialects must produce the same fingerprint, and ingesting the
  per-month file after the full dump must add zero rows
- Kind derivation asserted per dialect, including the regression that
  the full dialect must not classify everything as a transfer
- Re-ingest-is-a-no-op as an explicit property test
- Range-scoped reconciliation: removing a row from a re-exported range
  soft-deletes exactly that row
- FX resolver against a stubbed HTTP layer, covering each of the four
  source layers and the precision-disclosure calculation
- Analysis modules against a fixture DB with known expected outputs

## Out of scope

- No web server or hosted UI
- No live ZenMoney Diff API pull; the folder scan is the only ingest
  path until it is proven
- No forecasting or ML
- No changes to `src/` or the existing submit workflow
