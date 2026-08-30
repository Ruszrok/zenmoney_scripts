# Transaction Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make it impossible for the CLI to submit a ZenMoney transaction whose `categoryIds` or `accountId` belong to a different ZenMoney user than the current `PHPSESSID`'s user, by adding an unskippable validator that runs both at `--prepare` time (early-fail UX) and inside `submitTransactions` (final gate before the POST).

**Architecture:** A pure `src/validate.ts` module exports `validateTransactions()` which throws `ValidationError` carrying every issue it found. `src/api.ts:submitTransactions` is changed to take `ParsedTransaction[]` + `accountId`, internally fetches accounts and tag_groups, runs the validator, then converts and POSTs. `src/submit.ts:--prepare` calls the same validator before writing `data/review.json`. There is no flag, no parameter, and no caller path that bypasses the gate inside `submitTransactions`.

**Tech Stack:** TypeScript, Bun runtime, Bun's built-in `bun test` runner, no external test deps. Mocked `globalThis.fetch` in tests — no real network.

**Spec:** `docs/superpowers/specs/2026-05-08-transaction-validation-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/validate.ts` | create | Pure validator: `ValidationIssue`, `ValidationError`, `validateTransactions(parsed, accountId, validAccountIds, validTagGroupIds)`. No I/O. |
| `src/validate.test.ts` | create | Unit tests for the validator (16 cases). |
| `src/api.ts` | modify | `submitTransactions` signature change; `toZenMoney` becomes private; fetch + validate before POST. |
| `src/api.test.ts` | create | Integration tests with mocked `globalThis.fetch` (5 cases). |
| `src/submit.ts` | modify | Both call sites pass `(cookie, parsed, accountId)`; `--prepare` validates before writing `review.json`; top-level catch prints issue list when `ValidationError` is thrown. |
| `src/regression-review.test.ts` | create | Offline regression: current `data/review.json` must pass the validator. |
| `MEMORY.md` | modify | Append note: cached category IDs in this file are valid for the current PHPSESSID account only. |
| `CLAUDE.md` | modify | Note new validator behaviour in the workflow section. |

`config.ts` and `src/types.ts` are untouched.

**Commits:** the plan groups commits at logical boundaries. The user has asked Claude not to auto-commit; each "Commit checkpoint" tells the user *what* to commit at that point. Claude will not run `git commit` itself unless asked.

---

## Task 1: Write the failing test suite for `validateTransactions`

**Files:**
- Test: `src/validate.test.ts` (create)

- [ ] **Step 1.1: Create the test file with all 16 cases**

Create `src/validate.test.ts`:

```ts
import { describe, expect, test } from "bun:test";
import { validateTransactions, ValidationError } from "./validate";
import type { ParsedTransaction } from "./types";

const ACCOUNT = "11025256";
const VALID_ACCOUNTS = new Set([ACCOUNT, "11025255"]);
const VALID_TAGS = new Set<number>([650871, 650876, 30850494]);

function makeTxn(overrides: Partial<ParsedTransaction> = {}): ParsedTransaction {
  return {
    date: "08.05.2026",
    amount: 12.5,
    payee: "Lidl",
    comment: "",
    isIncome: false,
    categoryIds: [650871],
    ...overrides,
  };
}

describe("validateTransactions", () => {
  test("passes on empty array", () => {
    expect(() =>
      validateTransactions([], ACCOUNT, VALID_ACCOUNTS, VALID_TAGS),
    ).not.toThrow();
  });

  test("passes on a fully-valid transaction", () => {
    expect(() =>
      validateTransactions([makeTxn()], ACCOUNT, VALID_ACCOUNTS, VALID_TAGS),
    ).not.toThrow();
  });

  test("passes when categoryIds is empty (uncategorized)", () => {
    expect(() =>
      validateTransactions(
        [makeTxn({ categoryIds: [] })],
        ACCOUNT,
        VALID_ACCOUNTS,
        VALID_TAGS,
      ),
    ).not.toThrow();
  });

  test("fails when accountId is not in valid set", () => {
    let err: unknown;
    try {
      validateTransactions([makeTxn()], "99999", VALID_ACCOUNTS, VALID_TAGS);
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    const ve = err as ValidationError;
    expect(ve.issues).toHaveLength(1);
    expect(ve.issues[0]).toMatchObject({
      txnIndex: null,
      field: "account",
      value: "99999",
    });
    expect(ve.issues[0].reason).toMatch(/does not exist/i);
  });

  test("fails on a foreign categoryId", () => {
    let err: unknown;
    try {
      validateTransactions(
        [makeTxn({ categoryIds: [99999] })],
        ACCOUNT,
        VALID_ACCOUNTS,
        VALID_TAGS,
      );
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    const ve = err as ValidationError;
    expect(ve.issues).toHaveLength(1);
    expect(ve.issues[0]).toMatchObject({
      txnIndex: 0,
      field: "categoryIds",
      value: 99999,
    });
    expect(ve.issues[0].reason).toMatch(/different ZenMoney account/i);
  });

  test("reports only the foreign id when categoryIds is mixed", () => {
    let err: unknown;
    try {
      validateTransactions(
        [makeTxn({ categoryIds: [650871, 99999] })],
        ACCOUNT,
        VALID_ACCOUNTS,
        VALID_TAGS,
      );
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    const ve = err as ValidationError;
    expect(ve.issues).toHaveLength(1);
    expect(ve.issues[0].value).toBe(99999);
  });

  test("fails when categoryIds is not an array", () => {
    let err: unknown;
    try {
      validateTransactions(
        [makeTxn({ categoryIds: undefined as unknown as number[] })],
        ACCOUNT,
        VALID_ACCOUNTS,
        VALID_TAGS,
      );
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    expect((err as ValidationError).issues[0]).toMatchObject({
      txnIndex: 0,
      field: "categoryIds",
    });
    expect((err as ValidationError).issues[0].reason).toMatch(/array/i);
  });

  test("fails when categoryIds contains non-integers", () => {
    const cases: unknown[] = [NaN, 0.5, "650871", null, Infinity];
    for (const bad of cases) {
      let err: unknown;
      try {
        validateTransactions(
          [makeTxn({ categoryIds: [bad as number] })],
          ACCOUNT,
          VALID_ACCOUNTS,
          VALID_TAGS,
        );
      } catch (e) {
        err = e;
      }
      expect(err).toBeInstanceOf(ValidationError);
      expect((err as ValidationError).issues[0].field).toBe("categoryIds");
    }
  });

  test("fails when date format is wrong", () => {
    let err: unknown;
    try {
      validateTransactions(
        [makeTxn({ date: "5/8/2026" })],
        ACCOUNT,
        VALID_ACCOUNTS,
        VALID_TAGS,
      );
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    expect((err as ValidationError).issues[0]).toMatchObject({
      txnIndex: 0,
      field: "date",
    });
    expect((err as ValidationError).issues[0].reason).toMatch(/DD\.MM\.YYYY/);
  });

  test("fails when date is not a real calendar date", () => {
    for (const bad of ["32.05.2026", "30.02.2026", "00.01.2026", "01.13.2026"]) {
      let err: unknown;
      try {
        validateTransactions(
          [makeTxn({ date: bad })],
          ACCOUNT,
          VALID_ACCOUNTS,
          VALID_TAGS,
        );
      } catch (e) {
        err = e;
      }
      expect(err).toBeInstanceOf(ValidationError);
      expect((err as ValidationError).issues[0].field).toBe("date");
    }
  });

  test("fails when amount is not a positive finite number", () => {
    for (const bad of [0, -1, NaN, Infinity, -Infinity]) {
      let err: unknown;
      try {
        validateTransactions(
          [makeTxn({ amount: bad })],
          ACCOUNT,
          VALID_ACCOUNTS,
          VALID_TAGS,
        );
      } catch (e) {
        err = e;
      }
      expect(err).toBeInstanceOf(ValidationError);
      expect((err as ValidationError).issues[0].field).toBe("amount");
    }
  });

  test("fails on empty / whitespace-only payee", () => {
    for (const bad of ["", "   ", "\t\n"]) {
      let err: unknown;
      try {
        validateTransactions(
          [makeTxn({ payee: bad })],
          ACCOUNT,
          VALID_ACCOUNTS,
          VALID_TAGS,
        );
      } catch (e) {
        err = e;
      }
      expect(err).toBeInstanceOf(ValidationError);
      expect((err as ValidationError).issues[0].field).toBe("payee");
    }
  });

  test("fails when isIncome is not a boolean", () => {
    let err: unknown;
    try {
      validateTransactions(
        [makeTxn({ isIncome: "true" as unknown as boolean })],
        ACCOUNT,
        VALID_ACCOUNTS,
        VALID_TAGS,
      );
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    expect((err as ValidationError).issues[0].field).toBe("isIncome");
  });

  test("collects multiple issues across multiple transactions", () => {
    const txns = [
      makeTxn({ categoryIds: [99999] }),
      makeTxn({ date: "32.05.2026" }),
      makeTxn(),
      makeTxn({ amount: 0, payee: "" }),
    ];
    let err: unknown;
    try {
      validateTransactions(txns, ACCOUNT, VALID_ACCOUNTS, VALID_TAGS);
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    const ve = err as ValidationError;
    expect(ve.issues).toHaveLength(4);
    const indices = ve.issues.map((i) => i.txnIndex);
    expect(indices).toEqual([0, 1, 3, 3]);
  });

  test("error message includes issue count", () => {
    try {
      validateTransactions(
        [makeTxn({ amount: 0 }), makeTxn({ payee: "" })],
        ACCOUNT,
        VALID_ACCOUNTS,
        VALID_TAGS,
      );
    } catch (e) {
      expect((e as Error).message).toMatch(/2 issue\(s\)/);
    }
  });
});
```

- [ ] **Step 1.2: Run the suite — expect every test to fail**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test src/validate.test.ts`

Expected: all tests fail with `Cannot find module './validate'` or similar — `src/validate.ts` does not exist yet.

**Commit checkpoint (user-discretion):** add `src/validate.test.ts` only (red TDD step). Skip if you prefer one combined commit at end of Task 2.

---

## Task 2: Implement `src/validate.ts` until tests pass

**Files:**
- Create: `src/validate.ts`

- [ ] **Step 2.1: Write the implementation**

Create `src/validate.ts`:

```ts
import type { ParsedTransaction } from "./types";

export interface ValidationIssue {
  /** 0-based index in the input array, or null for batch-level issues. */
  txnIndex: number | null;
  /** Which field failed. */
  field:
    | "account"
    | "categoryIds"
    | "date"
    | "amount"
    | "payee"
    | "isIncome";
  /** The actual offending value, exactly as supplied. */
  value: unknown;
  /** Human-readable reason for the failure. */
  reason: string;
}

export class ValidationError extends Error {
  readonly issues: ValidationIssue[];
  constructor(issues: ValidationIssue[]) {
    super(`Transaction validation failed: ${issues.length} issue(s)`);
    this.name = "ValidationError";
    this.issues = issues;
  }
}

const DATE_RE = /^(\d{2})\.(\d{2})\.(\d{4})$/;

function isRealCalendarDate(s: string): boolean {
  const m = DATE_RE.exec(s);
  if (!m) return false;
  const day = Number(m[1]);
  const month = Number(m[2]);
  const year = Number(m[3]);
  const d = new Date(year, month - 1, day);
  return (
    d.getFullYear() === year &&
    d.getMonth() === month - 1 &&
    d.getDate() === day
  );
}

function isPositiveInteger(n: unknown): n is number {
  return typeof n === "number" && Number.isInteger(n) && n > 0;
}

function isPositiveFiniteAmount(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n) && n > 0;
}

/**
 * Throws ValidationError with every issue across the batch if anything is
 * invalid. Pure: no I/O. Caller fetches the live accounts + tag_groups.
 */
export function validateTransactions(
  transactions: ParsedTransaction[],
  accountId: string,
  validAccountIds: ReadonlySet<string>,
  validTagGroupIds: ReadonlySet<number>,
): void {
  const issues: ValidationIssue[] = [];

  if (!validAccountIds.has(accountId)) {
    issues.push({
      txnIndex: null,
      field: "account",
      value: accountId,
      reason: `account ${accountId} does not exist for this session — likely from a different ZenMoney account`,
    });
  }

  for (let i = 0; i < transactions.length; i++) {
    const t = transactions[i];

    if (!Array.isArray(t.categoryIds)) {
      issues.push({
        txnIndex: i,
        field: "categoryIds",
        value: t.categoryIds,
        reason: "categoryIds must be an array of integers",
      });
    } else {
      for (const id of t.categoryIds) {
        if (!isPositiveInteger(id)) {
          issues.push({
            txnIndex: i,
            field: "categoryIds",
            value: id,
            reason: "categoryIds must be an array of positive integers",
          });
          continue;
        }
        if (!validTagGroupIds.has(id)) {
          issues.push({
            txnIndex: i,
            field: "categoryIds",
            value: id,
            reason: `tag_group ${id} does not exist for this session — likely from a different ZenMoney account`,
          });
        }
      }
    }

    if (typeof t.date !== "string" || !DATE_RE.test(t.date)) {
      issues.push({
        txnIndex: i,
        field: "date",
        value: t.date,
        reason: "date must be DD.MM.YYYY",
      });
    } else if (!isRealCalendarDate(t.date)) {
      issues.push({
        txnIndex: i,
        field: "date",
        value: t.date,
        reason: "date is not a real calendar date",
      });
    }

    if (!isPositiveFiniteAmount(t.amount)) {
      issues.push({
        txnIndex: i,
        field: "amount",
        value: t.amount,
        reason: "amount must be a positive finite number",
      });
    }

    if (typeof t.payee !== "string" || t.payee.trim() === "") {
      issues.push({
        txnIndex: i,
        field: "payee",
        value: t.payee,
        reason: "payee must not be empty",
      });
    }

    if (typeof t.isIncome !== "boolean") {
      issues.push({
        txnIndex: i,
        field: "isIncome",
        value: t.isIncome,
        reason: "isIncome must be a boolean",
      });
    }
  }

  if (issues.length > 0) {
    throw new ValidationError(issues);
  }
}
```

- [ ] **Step 2.2: Run the suite — expect green**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test src/validate.test.ts`

Expected: all 16 tests pass.

- [ ] **Step 2.3: Run TypeScript check on the new file**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bunx tsc --noEmit src/validate.ts src/validate.test.ts`

Expected: no output (clean compile). If tsc isn't installed, run `bun x typescript` once to fetch it; otherwise rely on Bun's own type-aware test runner.

**Commit checkpoint (user-discretion):** `src/validate.ts` + `src/validate.test.ts` together — pure module, fully tested.

---

## Task 3: Write failing integration tests for `submitTransactions`

**Files:**
- Test: `src/api.test.ts` (create)

The new behaviour we want from `submitTransactions(cookie, parsed, accountId)`:

1. Calls `GET /v1/account/` and `GET /s1/profile/` (in any order — but both before validation).
2. Throws `ValidationError` *before* the POST if validation fails.
3. POSTs to `/v2/transaction/` with the right body if validation passes.

- [ ] **Step 3.1: Write the test file**

Create `src/api.test.ts`:

```ts
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  test,
} from "bun:test";
import { submitTransactions } from "./api";
import { ValidationError } from "./validate";
import type { ParsedTransaction } from "./types";

interface FetchCall {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string | null;
}

let calls: FetchCall[];
let queue: Array<() => Response>;
let originalFetch: typeof fetch;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  calls = [];
  queue = [];
  originalFetch = globalThis.fetch;
  globalThis.fetch = (async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = {};
    const ih = init?.headers ?? {};
    if (ih instanceof Headers) {
      ih.forEach((v, k) => {
        headers[k.toLowerCase()] = v;
      });
    } else if (Array.isArray(ih)) {
      for (const [k, v] of ih) headers[k.toLowerCase()] = v;
    } else {
      for (const [k, v] of Object.entries(ih))
        headers[k.toLowerCase()] = String(v);
    }
    const body =
      typeof init?.body === "string" ? init.body : init?.body == null ? null : String(init.body);
    calls.push({ url, method, headers, body });
    const next = queue.shift();
    if (!next) {
      throw new Error(`unexpected fetch call to ${url} (${method})`);
    }
    return next();
  }) as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

const COOKIE = "PHPSESSID=abc";
const ACCOUNT = "11025256";

const accountsBody = {
  [ACCOUNT]: { id: Number(ACCOUNT), title: "Bunq", type: "ccard", balance: 0, currency_id: 1 },
  "11025255": { id: 11025255, title: "Caixa", type: "ccard", balance: 0, currency_id: 1 },
};

const profileBody = {
  tags: { 100: { id: 100, title: "Еда" }, 101: { id: 101, title: "Продукты" } },
  tag_groups: {
    "650871": {
      id: 650871,
      tag0: 100,
      tag1: 101,
      tag2: null,
      show_outcome: true,
      show_income: false,
    },
  },
};

function txn(overrides: Partial<ParsedTransaction> = {}): ParsedTransaction {
  return {
    date: "08.05.2026",
    amount: 12.5,
    payee: "Lidl",
    comment: "",
    isIncome: false,
    categoryIds: [650871],
    ...overrides,
  };
}

describe("submitTransactions", () => {
  test("happy path: fetches accounts + profile, validates, then POSTs", async () => {
    queue.push(() => jsonResponse(accountsBody));
    queue.push(() => jsonResponse(profileBody));
    queue.push(() => jsonResponse({ ok: true, ids: [42] }));

    const result = await submitTransactions(COOKIE, [txn()], ACCOUNT);

    expect(result).toEqual({ ok: true, ids: [42] });

    const urls = calls.map((c) => `${c.method} ${c.url}`);
    expect(urls).toEqual([
      "GET https://zenmoney.ru/api/v1/account/",
      "GET https://zenmoney.ru/api/s1/profile/",
      "POST https://zenmoney.ru/api/v2/transaction/",
    ]);

    expect(calls[2].headers["cookie"]).toBe(COOKIE);
    expect(calls[2].headers["content-type"]).toBe(
      "application/x-www-form-urlencoded",
    );
    const sent = JSON.parse(calls[2].body ?? "[]");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      category: "0",
      tag_groups: ["650871"],
      income: 0,
      outcome: 12.5,
      date: "08.05.2026",
      payee: "Lidl",
      account_income: ACCOUNT,
      account_outcome: ACCOUNT,
    });
  });

  test("rejects foreign categoryId before POST", async () => {
    queue.push(() => jsonResponse(accountsBody));
    queue.push(() => jsonResponse(profileBody));

    let err: unknown;
    try {
      await submitTransactions(COOKIE, [txn({ categoryIds: [999999] })], ACCOUNT);
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    const methodUrls = calls.map((c) => `${c.method} ${c.url}`);
    expect(methodUrls).not.toContain(
      "POST https://zenmoney.ru/api/v2/transaction/",
    );
  });

  test("rejects unknown accountId before POST", async () => {
    queue.push(() => jsonResponse(accountsBody));
    queue.push(() => jsonResponse(profileBody));

    let err: unknown;
    try {
      await submitTransactions(COOKIE, [txn()], "99999");
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    const methodUrls = calls.map((c) => `${c.method} ${c.url}`);
    expect(methodUrls).not.toContain(
      "POST https://zenmoney.ru/api/v2/transaction/",
    );
  });

  test("propagates fetchAccounts failure without validating or POSTing", async () => {
    queue.push(() => new Response("nope", { status: 401 }));

    let err: Error | undefined;
    try {
      await submitTransactions(COOKIE, [txn()], ACCOUNT);
    } catch (e) {
      err = e as Error;
    }
    expect(err).toBeDefined();
    expect(err?.message).toMatch(/fetchAccounts failed: 401/);
    expect(calls).toHaveLength(1);
  });

  test("surfaces server failure on the POST", async () => {
    queue.push(() => jsonResponse(accountsBody));
    queue.push(() => jsonResponse(profileBody));
    queue.push(() => new Response("boom", { status: 500 }));

    let err: Error | undefined;
    try {
      await submitTransactions(COOKIE, [txn()], ACCOUNT);
    } catch (e) {
      err = e as Error;
    }
    expect(err).toBeDefined();
    expect(err?.message).toMatch(/submitTransactions failed: 500/);
    expect(err?.message).toMatch(/boom/);
  });
});
```

- [ ] **Step 3.2: Run — expect failure**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test src/api.test.ts`

Expected: most/all tests fail with type errors or argument-count errors — current `submitTransactions` takes `ZenMoneyTransaction[]`, not `(parsed, accountId)`.

**Commit checkpoint (user-discretion):** `src/api.test.ts` only.

---

## Task 4: Refactor `src/api.ts` to satisfy the new tests

**Files:**
- Modify: `src/api.ts`

- [ ] **Step 4.1: Replace `src/api.ts` with the new implementation**

Overwrite `src/api.ts`:

```ts
import { validateTransactions } from "./validate";
import type {
  ParsedTransaction,
  ZenMoneyAccount,
  ZenMoneyCategory,
  ZenMoneyTransaction,
} from "./types";

const BASE_URL = "https://zenmoney.ru/api";

function headers(cookie: string) {
  return {
    Cookie: cookie,
    "X-Requested-With": "XMLHttpRequest",
  };
}

export async function fetchAccounts(
  cookie: string,
): Promise<Record<string, ZenMoneyAccount>> {
  const res = await fetch(`${BASE_URL}/v1/account/`, {
    headers: headers(cookie),
  });
  if (!res.ok) throw new Error(`fetchAccounts failed: ${res.status}`);
  return res.json();
}

export async function fetchCategories(
  cookie: string,
): Promise<Record<string, ZenMoneyCategory>> {
  const res = await fetch(`${BASE_URL}/v1/category/`, {
    headers: headers(cookie),
  });
  if (!res.ok) throw new Error(`fetchCategories failed: ${res.status}`);
  return res.json();
}

/** Fetch full tag_groups hierarchy (with subcategories) from /api/s1/profile/ */
export async function fetchTagGroups(
  cookie: string,
): Promise<{ id: number; label: string; type: string }[]> {
  const res = await fetch(`${BASE_URL}/s1/profile/`, {
    headers: headers(cookie),
  });
  if (!res.ok) throw new Error(`fetchTagGroups failed: ${res.status}`);
  const data = await res.json();
  const tags: Record<number, string> = {};
  for (const t of Object.values(data.tags) as any[]) {
    tags[t.id] = t.title;
  }
  const results: { id: number; label: string; type: string }[] = [];
  for (const g of Object.values(data.tag_groups) as any[]) {
    const parts: string[] = [];
    if (g.tag0) parts.push(tags[g.tag0] ?? String(g.tag0));
    if (g.tag1) parts.push(tags[g.tag1] ?? String(g.tag1));
    if (g.tag2) parts.push(tags[g.tag2] ?? String(g.tag2));
    const label = parts.join(" / ");
    const type = g.show_outcome
      ? "expense"
      : g.show_income
      ? "income"
      : "hidden";
    results.push({ id: g.id, label, type });
  }
  results.sort((a, b) => a.label.localeCompare(b.label, "ru"));
  return results;
}

function toZenMoney(
  parsed: ParsedTransaction[],
  accountId: string,
): ZenMoneyTransaction[] {
  return parsed.map((t) => ({
    category: "0",
    tag_groups: (t.categoryIds || []).map(String),
    income: t.isIncome ? t.amount : 0,
    outcome: t.isIncome ? 0 : t.amount,
    date: t.date,
    comment: t.comment,
    payee: t.payee,
    account_income: accountId,
    account_outcome: accountId,
  }));
}

/**
 * Submits parsed transactions for the given account. ALWAYS validates first
 * against the live accounts + tag_groups for the session — there is no flag,
 * no parameter, and no caller path that bypasses this check.
 *
 * Throws ValidationError (with .issues) if any transaction is invalid.
 */
export async function submitTransactions(
  cookie: string,
  transactions: ParsedTransaction[],
  accountId: string,
): Promise<unknown> {
  const accounts = await fetchAccounts(cookie);
  const tagGroups = await fetchTagGroups(cookie);

  validateTransactions(
    transactions,
    accountId,
    new Set(Object.keys(accounts)),
    new Set(tagGroups.map((g) => g.id)),
  );

  const body = toZenMoney(transactions, accountId);
  const res = await fetch(`${BASE_URL}/v2/transaction/`, {
    method: "POST",
    headers: {
      ...headers(cookie),
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`submitTransactions failed: ${res.status} ${text}`);
  }
  return res.json();
}
```

- [ ] **Step 4.2: Run the API tests — expect green**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test src/api.test.ts`

Expected: all 5 tests pass.

- [ ] **Step 4.3: Run the full test suite to be sure nothing else broke**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test`

Expected: all tests across `src/validate.test.ts` + `src/api.test.ts` pass.

**Commit checkpoint (user-discretion):** `src/api.ts`. Validator now wired into the unskippable gate.

---

## Task 5: Update `src/submit.ts` — both call sites + early-fail in `--prepare`

**Files:**
- Modify: `src/submit.ts`

The CLI changes:

1. Remove the local `toZenMoney` function (it lives inside `src/api.ts` now).
2. `--submit-review` flow: pass `(cookie, review.transactions, review.account)`.
3. Legacy direct-submit flow: pass `(cookie, parsed, flags.account)`.
4. `--prepare`: after `fetchTagGroups`, also `fetchAccounts`, then call `validateTransactions` before writing `data/review.json`.
5. Top-level `catch`: if the error is a `ValidationError`, print `issues` table cleanly and exit 2; otherwise keep current behaviour.

- [ ] **Step 5.1: Replace `src/submit.ts`**

Overwrite `src/submit.ts`:

```ts
import {
  fetchAccounts,
  fetchCategories,
  fetchTagGroups,
  submitTransactions,
} from "./api";
import { ValidationError, validateTransactions } from "./validate";
import type {
  ParsedTransaction,
  ReviewFile,
} from "./types";
import { mkdirSync } from "fs";
import { join } from "path";

const DATA_DIR = join(import.meta.dir, "..", "data");
const REVIEW_FILE = join(DATA_DIR, "review.json");

function usage(): never {
  console.error(`Usage:
  bun run src/submit.ts --list-accounts --cookie "PHPSESSID=..."
  bun run src/submit.ts --list-categories --cookie "PHPSESSID=..."
  bun run src/submit.ts --prepare --cookie "PHPSESSID=..." --account "ID" <<< '[json]'
  bun run src/submit.ts --submit-review --cookie "PHPSESSID=..."
  bun run src/submit.ts [--dry-run] --cookie "PHPSESSID=..." --account "ID" <<< '[json]'`);
  process.exit(1);
}

function parseArgs(args: string[]) {
  const flags = {
    cookie: "",
    account: "",
    listAccounts: false,
    listCategories: false,
    dryRun: false,
    prepare: false,
    submitReview: false,
  };
  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case "--cookie":
        flags.cookie = args[++i] ?? "";
        break;
      case "--account":
        flags.account = args[++i] ?? "";
        break;
      case "--list-accounts":
        flags.listAccounts = true;
        break;
      case "--list-categories":
        flags.listCategories = true;
        break;
      case "--dry-run":
        flags.dryRun = true;
        break;
      case "--prepare":
        flags.prepare = true;
        break;
      case "--submit-review":
        flags.submitReview = true;
        break;
    }
  }
  return flags;
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of Bun.stdin.stream()) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf-8");
}

function printValidationError(err: ValidationError): void {
  console.error(err.message);
  for (const issue of err.issues) {
    const where = issue.txnIndex === null ? "batch" : `txn[${issue.txnIndex}]`;
    console.error(
      `  ${where} ${issue.field}=${JSON.stringify(issue.value)}: ${issue.reason}`,
    );
  }
}

async function main() {
  const flags = parseArgs(process.argv.slice(2));

  if (!flags.cookie) usage();

  if (flags.listAccounts) {
    const accounts = await fetchAccounts(flags.cookie);
    for (const [id, acc] of Object.entries(accounts)) {
      console.log(`${id}\t${acc.title}\t${acc.type}\tbalance: ${acc.balance}`);
    }
    return;
  }

  if (flags.listCategories) {
    const tagGroups = await fetchTagGroups(flags.cookie);
    for (const tg of tagGroups) {
      console.log(`${tg.id}\t${tg.label}\t${tg.type}`);
    }
    return;
  }

  // --prepare: fetch categories, validate, write review JSON
  if (flags.prepare) {
    if (!flags.account) {
      console.error("Error: --account is required for --prepare");
      usage();
    }

    const input = await readStdin();
    if (!input.trim()) {
      console.error("Error: no JSON input on stdin");
      usage();
    }

    let parsed: ParsedTransaction[];
    try {
      parsed = JSON.parse(input);
    } catch {
      console.error("Error: invalid JSON input");
      process.exit(1);
    }

    const accounts = await fetchAccounts(flags.cookie);
    const tagGroups = await fetchTagGroups(flags.cookie);

    validateTransactions(
      parsed,
      flags.account,
      new Set(Object.keys(accounts)),
      new Set(tagGroups.map((g) => g.id)),
    );

    const review: ReviewFile = {
      account: flags.account,
      categories: tagGroups,
      transactions: parsed,
    };

    mkdirSync(DATA_DIR, { recursive: true });
    await Bun.write(REVIEW_FILE, JSON.stringify(review, null, 2) + "\n");
    console.log(`Review file written to ${REVIEW_FILE}`);
    console.log(
      `${parsed.length} transaction(s), ${tagGroups.length} categories`,
    );
    return;
  }

  // --submit-review: read review JSON and submit
  if (flags.submitReview) {
    const file = Bun.file(REVIEW_FILE);
    if (!(await file.exists())) {
      console.error(`Error: review file not found at ${REVIEW_FILE}`);
      console.error("Run --prepare first to generate it.");
      process.exit(1);
    }

    const review: ReviewFile = await file.json();

    console.log(`Submitting ${review.transactions.length} transaction(s)...`);
    const result = await submitTransactions(
      flags.cookie,
      review.transactions,
      review.account,
    );
    console.log("Success:", JSON.stringify(result, null, 2));
    return;
  }

  // Direct submit from stdin (legacy)
  if (!flags.account) {
    console.error("Error: --account is required for submitting transactions");
    usage();
  }

  const input = await readStdin();
  if (!input.trim()) {
    console.error("Error: no JSON input on stdin");
    usage();
  }

  let parsed: ParsedTransaction[];
  try {
    parsed = JSON.parse(input);
  } catch {
    console.error("Error: invalid JSON input");
    process.exit(1);
  }

  if (flags.dryRun) {
    // Dry-run still validates so the user sees the same errors they would on submit.
    const accounts = await fetchAccounts(flags.cookie);
    const tagGroups = await fetchTagGroups(flags.cookie);
    validateTransactions(
      parsed,
      flags.account,
      new Set(Object.keys(accounts)),
      new Set(tagGroups.map((g) => g.id)),
    );
    console.log("=== DRY RUN — validated, not submitting ===");
    console.log(JSON.stringify(parsed, null, 2));
    return;
  }

  console.log(`Submitting ${parsed.length} transaction(s)...`);
  const result = await submitTransactions(
    flags.cookie,
    parsed,
    flags.account,
  );
  console.log("Success:", JSON.stringify(result, null, 2));
}

main().catch((err) => {
  if (err instanceof ValidationError) {
    printValidationError(err);
    process.exit(2);
  }
  console.error("Error:", err.message);
  process.exit(1);
});
```

- [ ] **Step 5.2: Run TypeScript check on the whole repo**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bunx --bun tsc --noEmit -p .`

Expected: no errors. (If `tsc` is not installed, `bunx` will fetch it on first run.)

- [ ] **Step 5.3: Run the full test suite**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test`

Expected: 21 tests pass (16 validator + 5 api).

**Commit checkpoint (user-discretion):** `src/submit.ts`. CLI now validates twice on the prepare→submit path (once in `--prepare`, once inside `submitTransactions`), and the dry-run path also validates.

---

## Task 6: Regression test — current `data/review.json` must still pass

**Files:**
- Create: `src/regression-review.test.ts`

The point of this test is the user's "verify last submission won't fail" requirement, executed entirely offline.

- [ ] **Step 6.1: Write the regression test**

Create `src/regression-review.test.ts`:

```ts
import { describe, expect, test } from "bun:test";
import { readFileSync, existsSync } from "fs";
import { join } from "path";
import { validateTransactions } from "./validate";
import type { ReviewFile } from "./types";

const REVIEW_PATH = join(import.meta.dir, "..", "data", "review.json");

describe("regression: data/review.json", () => {
  test("current review.json passes the new validator (offline)", () => {
    if (!existsSync(REVIEW_PATH)) {
      // No review file checked in — nothing to regress against.
      return;
    }
    const review: ReviewFile = JSON.parse(readFileSync(REVIEW_PATH, "utf-8"));
    const accountIds = new Set([review.account]);
    const tagGroupIds = new Set(review.categories.map((c) => c.id));

    expect(() =>
      validateTransactions(
        review.transactions,
        review.account,
        accountIds,
        tagGroupIds,
      ),
    ).not.toThrow();
  });
});
```

- [ ] **Step 6.2: Run only the regression test**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test src/regression-review.test.ts`

Expected: 1 test passes. If `data/review.json` is missing, the test silently passes (it's a no-op when there's nothing to regress).

- [ ] **Step 6.3: Run the full suite**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test`

Expected: 22 tests pass.

**Commit checkpoint (user-discretion):** `src/regression-review.test.ts`.

---

## Task 7: Update `MEMORY.md` and `CLAUDE.md`

**Files:**
- Modify: `MEMORY.md` (project root memory index)
- Modify: `CLAUDE.md`
- Create: `/Users/vyudanov/.claude/projects/-Users-vyudanov-development-zenmoney-scripts/memory/feedback_per_account_categories.md`

- [ ] **Step 7.1: Add a feedback memory file**

Create `/Users/vyudanov/.claude/projects/-Users-vyudanov-development-zenmoney-scripts/memory/feedback_per_account_categories.md`:

```markdown
---
name: ZenMoney category IDs are per-account, never reuse cached IDs across sessions
description: tag_group IDs in MEMORY.md are valid only for the PHPSESSID they were captured against; submitting them under a different session corrupts the iOS app's local DB
type: feedback
---

ZenMoney's tag_group / category IDs are per-user. The cached "Payee → Category" table in this MEMORY.md was populated from one session. If the current PHPSESSID is for a different ZenMoney account, every cached ID is wrong for that account.

**Why:** ZenMoney support confirmed that submissions with foreign tag_group IDs are accepted by the server, but the iOS client de-syncs (manifests as `BUG IN CLIENT OF libsqlite3.dylib: vnode unlinked while in use` plus "Unable to connect to server" — see `support_ticket.md` and `project_ios_login_issue.md`).

**How to apply:**
- Always derive the `categoryId` for a payee by matching `categoryName` against the live `categories` array in `data/review.json` (which `--prepare` populates from the current session). Don't read the IDs straight out of the MEMORY.md table.
- The validator in `src/validate.ts` will hard-fail any `--prepare` or submit that uses a foreign ID. Do not bypass it.
- If MEMORY.md's cached IDs and the live `categories` ever disagree, treat the live array as the source of truth and update MEMORY.md afterwards.
```

- [ ] **Step 7.2: Add a pointer line to `MEMORY.md`**

Edit `/Users/vyudanov/.claude/projects/-Users-vyudanov-development-zenmoney-scripts/memory/MEMORY.md`. Find the "Feedback" section:

```
## Feedback
- [Unicode payees](feedback_unicode_payees.md) — avoid diacritics and & in payee names, breaks iOS sync
```

Replace it with:

```
## Feedback
- [Unicode payees](feedback_unicode_payees.md) — avoid diacritics and & in payee names, breaks iOS sync
- [Per-account categories](feedback_per_account_categories.md) — tag_group IDs are per-user; never use cached IDs against a different PHPSESSID; src/validate.ts hard-fails foreign IDs
```

- [ ] **Step 7.3: Update `CLAUDE.md` workflow**

Edit `CLAUDE.md`. In section "### 4. Categorize transactions", **before** the existing "Category mapping hints" table, add this paragraph:

```markdown
**Important:** ZenMoney `tag_group` IDs are per-user. The hints table below is a *cache* — every run of `--prepare` and `--submit-review` validates `categoryIds` against the live tag_groups for the session and **hard-fails** if any ID doesn't belong. If you see a `ValidationError` listing foreign IDs, the table is stale for the current `PHPSESSID`; rebuild your category choices from the `categories` array in the freshly-written `data/review.json` and update the table afterwards.
```

In section "### 7. Submit from review file", append a sentence:

```markdown
The submit path validates a second time inside `submitTransactions` (`src/api.ts`) using a fresh fetch of accounts + tag_groups, so any post-`--prepare` edits you made to `data/review.json` are still checked. There is no flag to disable this.
```

- [ ] **Step 7.4: Run all tests once more to be sure docs edits didn't touch code paths**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test`

Expected: 22 tests pass.

**Commit checkpoint (user-discretion):** `MEMORY.md`, `CLAUDE.md`, plus the new memory file under `~/.claude/.../memory/`.

---

## Task 8: Final verification (offline)

- [ ] **Step 8.1: Type-check the whole repo**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bunx --bun tsc --noEmit -p .`

Expected: clean — no errors.

- [ ] **Step 8.2: Run the whole test suite**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && bun test`

Expected: all 22 tests pass.

- [ ] **Step 8.3: Confirm there is no remaining unvalidated submit path**

Search the repo for direct calls to `fetch(` against `/v2/transaction/`:

Run: `cd /Users/vyudanov/development/zenmoney_scripts && grep -RnE 'v2/transaction|submitTransactions' src/`

Expected output mentions only:
- the definition in `src/api.ts`
- the two CLI call sites in `src/submit.ts` (`--submit-review` and the legacy direct-submit path)
- the test file `src/api.test.ts`

Anything else (a new caller, a leftover script) means a path may bypass the validator and needs to be added to the gate or deleted.

- [ ] **Step 8.4: Confirm `data/review.json` is unchanged on disk**

Run: `cd /Users/vyudanov/development/zenmoney_scripts && git status data/`

Expected: no changes to `data/`. The plan must not have touched the user's pending review file.

- [ ] **Step 8.5: Read `support_ticket.md` once more to confirm the original incident is addressed**

Open `support_ticket.md` and confirm the "What I tried, and what I'd like from you" section's third ask is now satisfiable: the local CLI no longer permits the failure mode that caused the iOS corruption.

**Done.** No real network calls have been made by this plan. The user can later run, on their own, against a real session:
```
bun run src/submit.ts --prepare --cookie "PHPSESSID=…" --account "11025256" <<< '<json>'
bun run src/submit.ts --submit-review --cookie "PHPSESSID=…"
```
…and observe that everything either validates or fails fast with an issue list.
