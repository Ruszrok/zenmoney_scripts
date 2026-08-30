# Transaction Validation — Design Spec

**Date:** 2026-05-08
**Status:** Draft, awaiting user review
**Owner:** vyudanov

## Problem

ZenMoney support confirmed that some prior submissions to this account contained `tag_groups` IDs that belong to a *different* ZenMoney user. Submissions like that get accepted by the server but corrupt the iOS app's local state on next sync — manifesting as the `vnode unlinked while in use` SQLite cascade and the "Unable to connect to server" UI error analysed in `support_ticket.md`.

Today the CLI in this repo has zero validation:

- `src/submit.ts:61-76` — `toZenMoney()` passes whatever `categoryIds` are in the input JSON straight through into the request body.
- `src/submit.ts:108-143` — `--prepare` fetches the live category list and writes it into `data/review.json` next to the user-edited transactions, but never asserts that every `categoryId` actually appears in that list.
- `src/api.ts:59-76` — `submitTransactions()` does the POST without any consistency check.

Claude's screenshot pipeline assigns `categoryIds` from a hardcoded mapping in `MEMORY.md` / `CLAUDE.md`. Those IDs were captured from one session; if they ever drift from the current `PHPSESSID`'s user (multi-account use, account reset, etc.) the script silently submits foreign IDs.

## Goal

Make it impossible for the CLI to submit a transaction whose `categoryIds` or `account` don't belong to the current `PHPSESSID`'s ZenMoney user. The user must not be able to skip this check.

## Non-goals

- Auto-translating foreign IDs to live IDs by `categoryName` (rejected as Approach 3 during brainstorming — silent translation can mask mistakes).
- Server-side fixes (out of scope for this repo).
- Repairing previously-submitted bad data (separate concern).
- Validating the iOS app's own behaviour.
- Diacritic / Unicode warnings on `payee` (already covered by the "Unicode payees" feedback memory; orthogonal to this spec).

## Approach

Approach selected during brainstorming: **single shared validator, called in two places** (`--prepare` for fast feedback, and inside `submitTransactions` for the unskippable last gate). Failure mode: **hard-fail the entire batch** on the first violation across all transactions, reporting every issue at once.

### Components

```
                    ┌────────────────────────────────────────────┐
                    │       src/validate.ts (pure, sync)         │
                    │                                            │
                    │  validateTransactions(                     │
                    │    txns: ParsedTransaction[],              │
                    │    accountId: string,                      │
                    │    accountIds: ReadonlySet<string>,        │
                    │    tagGroupIds: ReadonlySet<number>,       │
                    │  ): void   // throws ValidationError       │
                    │                                            │
                    │  class ValidationError extends Error {     │
                    │    issues: ValidationIssue[];              │
                    │  }                                         │
                    └────────────────────────────────────────────┘
                              ▲                          ▲
                              │                          │
              early-fail UX   │                          │  unskippable gate
                              │                          │
            ┌─────────────────┴────┐         ┌───────────┴───────────────┐
            │ src/submit.ts        │         │ src/api.ts                │
            │   --prepare flow:    │         │   submitTransactions(     │
            │     fetchAccounts()  │         │     cookie,               │
            │     fetchTagGroups() │         │     parsed,               │
            │     validate(…)      │         │     accountId)            │
            │     write review.json│         │       ↓                   │
            └──────────────────────┘         │   fetchAccounts()         │
                                             │   fetchTagGroups()        │
                                             │   validate(…)  ◄ throws   │
                                             │   toZenMoney(parsed,      │
                                             │              accountId)   │
                                             │   POST /v2/transaction/   │
                                             └───────────────────────────┘
```

### Why both call sites

- **`--prepare`** validates *before writing `data/review.json`*. The user gets a clear error report instead of an opaque file with bad IDs they have to debug later.
- **`submitTransactions`** validates *immediately before the POST*, fetching fresh accounts/tag_groups inside the function. This is the last gate. There is no flag, no parameter, and no caller path that bypasses it; if you import `submitTransactions`, you get validation. The fresh fetch is critical: between `--prepare` and `--submit-review` the user might edit `review.json` (that's the whole point of the review file), so we cannot trust the snapshot embedded in `review.json`.

### Why hard-fail batch (not skip-and-continue)

Partial submission is exactly how the bad state was created in the first place. If the user has 8 transactions and 2 reference foreign categories, dropping those 2 silently means 6 land on the server fine but the user thinks the run was clean and may not notice the dropped pair until the iOS app misbehaves again. Hard-fail forces visibility: nothing is submitted until the input is clean.

### Rule set (v1)

Each rule produces a `ValidationIssue` if violated. **All issues are collected** before throwing, so the user sees every problem in one report.

| Rule | Field | Trigger | Reason text |
|---|---|---|---|
| 1 | `account` (script-level, applies to all txns in batch) | `accountId` not in `accountIds` set | `account ${id} does not exist for this session` |
| 2 | `categoryIds` | any element of `t.categoryIds` not in `tagGroupIds` set | `tag_group ${id} does not exist for this session — likely from a different ZenMoney account` |
| 3 | `categoryIds` | not an array, or contains non-integer / NaN | `categoryIds must be an array of integers` |
| 4 | `date` | doesn't match `/^\d{2}\.\d{2}\.\d{4}$/` | `date must be DD.MM.YYYY` |
| 5 | `date` | matches format but parses to invalid calendar date (e.g. 32.05.2026, 30.02.2026) | `date is not a real calendar date` |
| 6 | `amount` | not finite, ≤ 0, or NaN | `amount must be a positive finite number` |
| 7 | `payee` | empty / whitespace-only after trim | `payee must not be empty` |
| 8 | `isIncome` | not strictly `true` or `false` | `isIncome must be a boolean` |

Rule 1 fires once for the whole batch (it's a script-level check), the rest fire per-transaction with `txnIndex`.

**Deliberately deferred** (so the spec stays focused; YAGNI):
- Diacritic / `&` warnings in `payee`. Memory note records this is a soft-warn concern; out of scope here.
- Comment length cap (no documented server limit).
- Far-future / far-past date sanity check.
- Cross-field rules (e.g. `account_income == account_outcome` for non-transfers). The current `toZenMoney` always sets them equal, so there's nothing to validate yet.

### Error model

```ts
// src/validate.ts
export interface ValidationIssue {
  txnIndex: number | null;   // null = batch-level (rule 1); else 0-based index in input
  field: string;             // 'account' | 'categoryIds' | 'date' | 'amount' | 'payee' | 'isIncome'
  value: unknown;            // the actual offending value
  reason: string;            // human-readable reason
}

export class ValidationError extends Error {
  readonly issues: ValidationIssue[];
  constructor(issues: ValidationIssue[]) {
    super(`Transaction validation failed: ${issues.length} issue(s)`);
    this.issues = issues;
    this.name = "ValidationError";
  }
}

export function validateTransactions(
  transactions: ParsedTransaction[],
  accountId: string,
  validAccountIds: ReadonlySet<string>,
  validTagGroupIds: ReadonlySet<number>,
): void;
```

CLI catches `ValidationError`, prints all issues, exits 2.

### API surface change in `submitTransactions`

**Before:**
```ts
submitTransactions(cookie: string, transactions: ZenMoneyTransaction[]): Promise<unknown>
```

**After:**
```ts
submitTransactions(
  cookie: string,
  transactions: ParsedTransaction[],
  accountId: string,
): Promise<unknown>
```

The conversion `ParsedTransaction → ZenMoneyTransaction` (`toZenMoney`) moves *into* `src/api.ts` as a private helper. The function does:

1. `fetchAccounts(cookie)` → `Set<string>` of account IDs.
2. `fetchTagGroups(cookie)` → `Set<number>` of tag_group IDs.
3. `validateTransactions(parsed, accountId, accountIds, tagGroupIds)` — throws on any issue.
4. Convert to `ZenMoneyTransaction[]`.
5. POST to `/v2/transaction/`.

Two call sites in `src/submit.ts` (the `--submit-review` and legacy direct paths) update to pass `(cookie, parsed, accountId)`. The `toZenMoney` export is removed (it becomes private).

This is an **intentionally breaking change** to the in-repo API; the module is not consumed externally.

### Test plan

Tests use Bun's built-in `bun test`. No real network calls.

#### `src/validate.test.ts` (pure unit tests)

| # | Test | Expected |
|---|---|---|
| 1 | empty `transactions` array | passes (no throw) |
| 2 | single fully-valid transaction | passes |
| 3 | transaction with empty `categoryIds: []` | passes (uncategorized is allowed) |
| 4 | `accountId` not in `validAccountIds` | throws, 1 batch-level issue, `txnIndex: null` |
| 5 | one foreign categoryId | throws, 1 issue with `txnIndex: 0`, `field: 'categoryIds'`, `value: 99999` |
| 6 | mix of valid + foreign categoryIds in one txn | throws, only the foreign one reported |
| 7 | `categoryIds` is `null` / `undefined` / not an array | throws, `field: 'categoryIds'`, reason mentions "array" |
| 8 | `categoryIds` contains non-integer (`NaN`, `0.5`, `"x"`) | throws |
| 9 | `date: '32.05.2026'` | throws, `field: 'date'`, reason "not a real calendar date" |
| 10 | `date: '5/8/2026'` | throws, `field: 'date'`, reason "must be DD.MM.YYYY" |
| 11 | `date: '30.02.2026'` (valid format, invalid date) | throws |
| 12 | `amount: 0`, `amount: -1`, `amount: NaN`, `amount: Infinity` | each throws, `field: 'amount'` |
| 13 | `payee: ''`, `payee: '   '` | throws, `field: 'payee'` |
| 14 | `isIncome: 'true'` (string instead of bool) | throws, `field: 'isIncome'` |
| 15 | multiple issues across multiple txns | throws once, `issues.length === total`, txnIndex order preserved |
| 16 | error message contains issue count | matches `/2 issue\(s\)/` etc. |

#### `src/api.test.ts` (integration with mocked `fetch`)

`globalThis.fetch` is overridden per-test with a stub that records calls and returns canned responses.

| # | Test | Expected |
|---|---|---|
| A | happy path: fetch + validate + POST, all valid | exactly 3 fetch calls in order: `/v1/account/`, `/s1/profile/`, `/v2/transaction/` (POST). Returns the POST's JSON. |
| B | foreign categoryId | throws `ValidationError` *before* the POST is made — assert no fetch call to `/v2/transaction/` |
| C | unknown accountId | throws `ValidationError` before POST |
| D | `fetchAccounts` returns 401 | throws `Error("fetchAccounts failed: 401")`, no validate, no POST |
| E | server returns 500 on POST | error message includes status + body text |

#### `src/regression-review.test.ts` (current review.json must still validate)

A targeted regression that proves the user's existing `data/review.json` passes the new validator without any network call:

```ts
const review = JSON.parse(readFileSync('data/review.json', 'utf-8'));
const accountIds = new Set([review.account]);  // assumed valid for this test
const tagGroupIds = new Set(review.categories.map(c => c.id));
expect(() =>
  validateTransactions(review.transactions, review.account, accountIds, tagGroupIds)
).not.toThrow();
```

This is the user's "verify last submission won't fail" check, run entirely offline.

### File changes

| File | Change |
|---|---|
| `src/validate.ts` | **new** — `ValidationIssue`, `ValidationError`, `validateTransactions` |
| `src/validate.test.ts` | **new** — unit tests (~16 cases) |
| `src/api.ts` | modify — `submitTransactions` signature change, add `toZenMoney` as internal helper, fetch accounts+tag_groups+validate before POST |
| `src/api.test.ts` | **new** — integration tests with mocked `fetch` (~5 cases) |
| `src/submit.ts` | modify — both call sites pass `(cookie, parsed, accountId)`; `--prepare` calls `validateTransactions` after `fetchTagGroups` (early-fail); CLI top-level `catch` recognises `ValidationError` and prints `issues` table, exits 2 |
| `src/regression-review.test.ts` | **new** — verifies current `data/review.json` |
| `MEMORY.md` | append a feedback memory: cached category IDs in this file are valid for the *current* PHPSESSID account only |
| `CLAUDE.md` | note new validator behaviour in the workflow section |

`config.ts`, `src/types.ts` unchanged.

### Verification before completion

Per user instruction, **no real ZenMoney API calls during verification**.

1. `bun test` — all new tests pass.
2. `bunx tsc --noEmit` (or equivalent) — TypeScript compiles.
3. The regression test on `data/review.json` passes — proves the existing review file would not be rejected by the new validator.
4. Read-through of the diff to confirm there's no remaining path that submits without validating.

If the user later wants to do a live end-to-end check, they can run `--list-categories` against a fresh `PHPSESSID` and `--prepare` against the same cookie — but that is **not** part of this spec's verification.

## Risks and mitigations

- **Risk:** validator rejects the user's *next* legit submission because of a rule that's too strict (e.g. a date format the user didn't expect to see). **Mitigation:** rules are derived from the existing `ParsedTransaction` contract (`date: DD.MM.YYYY`, `amount: positive number`, etc.) and from the current `data/review.json` shape; the regression test pins this.
- **Risk:** breaking `submitTransactions` signature breaks an external caller. **Mitigation:** the only callers are inside this repo (two in `src/submit.ts`); the module is not published.
- **Risk:** extra fetches per submit add latency. **Mitigation:** human-driven submits (one per banking-screenshot batch) — extra ~100ms is irrelevant.
- **Risk:** tests rely on Bun-specific `fetch` mocking. **Mitigation:** monkey-patch `globalThis.fetch` and restore in `afterEach` — a portable pattern that works in Bun's test runner without extra deps.
