---
name: finance-advisor
description: Use when the user asks for financial analysis, budget advice, spending trends, savings rate, subscription/recurring-charge review, or "how am I doing" questions about their money. Reads the SQLite finance warehouse built from ZenMoney CSV exports (`finance/`, `data/finance.db`). Triggers — "analyse my spending", "financial advice", "budget review", "where is my money going", "how much do I spend on X", "какие у меня траты", "проанализируй бюджет", "на что я трачу деньги", "сколько я откладываю".
---

# Financial advisor

Interpret the numbers. Do not merely restate them — the user can read a table.
This warehouse has several traps that a naive read of the figures falls
into; section 3 exists specifically to keep you out of them. Read it before
writing a word of advice.

## 1. Refresh and verify

```bash
python3 -m finance ingest        # picks up anything new in data/dumps/
python3 -m finance fx --refresh  # only if ingest reported new rows
python3 -m finance verify
```

Read the coverage line. **Every figure you quote must name its real cutoff
month** — do not imply the data runs to today. If `verify` reports missing
months, say so before any conclusion — a gap in the middle of a trend
invalidates the trend.

If `fx` reports `unresolved` above zero, name the affected currencies and
dates and tell the user to add them to `fx_overrides.toml`.

## 2. Pull the analysis

```bash
python3 -m finance report --months 24 --json
```

This is the whole payload: `coverage`, `fx_precision` (this window) and
`fx_precision_lifetime`, `fx_notes`, `cashflow`/`cashflow_3m`/`cashflow_12m`,
`savings_rate_12m`, `drift`, `uncategorised`, `year_over_year`, `recurring`
(one row per cluster, with a `status` of `active`/`new`/`dormant`),
`recurring_active_total_eur`/`recurring_active_count`,
`recurring_dormant_total_eur`/`recurring_dormant_count`, `budget`,
`outliers`, `spend_outlier_months`, and `net_flow` (each account carries
`is_true_balance` — see rule 4). Read it, then reason; don't just print it.

For anything the payload doesn't answer, query the views directly:

```bash
python3 -m finance query "SELECT month, SUM(spend_eur) FROM v_monthly GROUP BY month ORDER BY month DESC LIMIT 12"
```

`v_spend` already excludes transfers, `Корректировка`, and moves into
savings/investment accounts. `v_income` flags passive interest separately.
**Never compute spending off the raw `transactions` table** — transfers get
double-counted.

## 3. Interpret — rules that keep the advice honest

These were each verified against this user's real 17,397-row history, not
assumed. A few contradict what the raw numbers suggest at a glance — that's
exactly why they're rules and not left to judgment each time.

1. **A single month's savings rate is noise; lead with `savings_rate_12m`.**
   Income here is lumpy contractor payments, not salary — a `Зарплата` row
   can land as two large payments in one month and then nothing for three.
   A month with no invoice paid can show a rate like ‑2500%+. Never quote a
   single month's savings rate as if it means something on its own; always
   pair it with, or replace it by, the trailing-12-month figure.

2. **Recurring load = `status in (active, new)` only. Dormant is a separate,
   still-worth-showing list, never part of the headline total.** A
   cluster-detection pass over recurring charges typically finds a large
   minority that are cancelled or lapsed — summing everything the detector
   found overstates the user's real ongoing commitment, sometimes by more
   than double. Report the ongoing figure (`recurring_active_total_eur` +
   the `new`-status rows) as "your monthly commitment," and show the
   dormant list underneath as "no longer charging" — useful, but not a
   current cost.

3. **The largest monthly outliers are usually annual tax, not overspending.**
   Check `spend_outlier_months` and `outliers` for a `Налоги и пошлины` (or
   `Квартплата / Переезд`-type one-off) category before writing anything
   that reads as "you overspent this month." Explain the concentration and
   flag the planning implication — a five-figure obligation landing in a
   single month is a cash-flow-timing problem, not a lifestyle one.

4. **Never call `net_flow` a balance or a net worth figure.** Check
   `is_true_balance` per account in the payload — if it's `false` (accounts
   without an `opening_balance` set in `accounts.toml`), the number is net
   flow *since the account's first transaction*, not a balance. Do not sum
   accounts into a "net worth" total. If the user wants runway, tell them
   what unlocks it: filling in `opening_balance_minor` for their accounts in
   `accounts.toml`. Runway itself is not implemented.

5. **Quote FX precision volume-weighted, and only when it matters.** Use
   `fx_precision` (the window) over `fx_precision_lifetime` when talking
   about recent totals — it says what share of the *euros*, not the rows,
   rest on published rates (ecb + base) versus filled/implied estimates.
   Mention it whenever a total leans meaningfully on RUB or KZT; read
   `fx_notes` for the specific caveats it already flags (e.g. a currency
   with no published ECB rate, or one that stopped publishing after a given
   date) and pass those along rather than re-deriving them.

6. **`(uncategorised)` is a bookkeeping signal, not a spending category.**
   It regularly shows up in `drift` and `budget` with a large ratio because
   the baseline is near zero. Report it as "spending you haven't
   categorised yet," with the euro amount, and suggest categorising it —
   never fold it into "you're spending more on X" as if it were a real
   category, and never recommend cutting it.

7. **`TRF CXDAPP` (and similarly transfer-shaped labels that recur monthly
   at a stable amount) can be a real expense, not a self-transfer.** The
   screenshot-import workflow in this file's category-hints table treats
   `TRF CXDAPP` as a self-transfer to skip — that rule is scoped to
   *importing new ZenMoney transactions from screenshots*. In the
   warehouse, rows already categorised (e.g. under `Квартплата / Аренда`)
   with no matching income leg are real spend, often the single largest
   recurring line. Check the category and the presence of an income-side
   match before calling anything a transfer; don't assume from the label.

8. **A raw year-over-year comparison understates the current year** if it
   isn't complete — check `coverage`'s last month against December; if the
   current year has N months of data, its total is against an implicit
   12/N undercount versus a full prior year. Either annualise
   (`year_over_year[cat][this_year] * 12 / N`) or say plainly that the
   comparison is partial.

9. **Payee is empty for a multi-year stretch in the middle of the history**
   (roughly 2020–2025 in this data — confirm the actual sparse range with
   `SELECT strftime('%Y', date), COUNT(*) FILTER (WHERE payee != ''), COUNT(*)
   FROM v_transactions GROUP BY 1`). Do not claim merchant-level insight
   ("you spend most at X") for years where payee coverage is in the low
   single digits percent — category-level is all that's honest there.

10. Older transfer-side currency inference can be wrong for a handful of
    very old rows (legacy `Debts`-account transfers from years before both
    exports declared a currency on every row) — immaterial to anything
    recent, but if the user asks about a specific pre-2017 debt transfer,
    flag that its currency was inferred, not declared.

## 4. Write the advisory

Cover, in this order, and **lead each section with the conclusion**, not the
table:

1. **Where the money went** — the shape of the last 24 months, not a
   category dump.
2. **What changed** — drift, with the honest question of whether it was a
   choice (e.g. rent renegotiated, a subscription added) or just noise.
3. **Recurring load** — the ongoing commitment per rule 2, plus the dormant
   list, plus anything that's quietly risen within `recurring`.
4. **Savings rate** — the 12-month trend per rule 1, and what earned income
   alone gives without passive interest (`savings_rate_12m_income_eur` vs
   `savings_rate_12m_expense_eur`, and `cashflow`'s `earned_eur` vs
   `passive_eur` split).
5. **What to do** — at most three concrete actions, each with the euro
   figure it frees **per year**, derived from the user's own trimmed
   history (see the do-not list below).

## 5. Deliver

Write the markdown report:

```bash
python3 -m finance report --months 24 --out reports/$(date +%F)-advisory.md
```

(`reports/` is gitignored — it holds real financial data and stays local.)

Then publish an Artifact dashboard:

- **Load the `dataviz` skill before writing any chart code**, and load
  `artifact-design` before writing the page.
- Charts worth having: monthly spend with 3- and 12-month trailing means;
  savings rate over time with earned income separated from passive; the
  drift table as a slope chart (recent 6m against prior 12m); recurring
  load as a stacked monthly commitment (active/new vs. dormant, so the
  contrast from rule 2 is visible at a glance).
- Charts are **inline SVG** — no charting library. The page must work
  offline and the CSP allowlist does not cover arbitrary CDNs for anything
  but a script tag from the approved hosts.

Hand the user the Artifact link and the report file path.

## Do not

- Do not invent budget targets — `budget` already derives them from the
  user's own trimmed history; never substitute a round number you picked.
- Do not claim merchant-level insight for years where payee is sparsely
  populated (rule 9).
- Do not present dormant recurring charges as current costs (rule 2).
- Do not quote a single month's savings rate as if it were meaningful on
  its own (rule 1).
- Do not sum `net_flow` across accounts into a net-worth or balance figure
  (rule 4).
- Do not compute spending from the raw `transactions` table — use `v_spend`.
