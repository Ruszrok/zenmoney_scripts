import { describe, expect, test } from "bun:test";
import { toDiffTransactions } from "./diff-api";
import { ensureIds } from "./idempotency";
import type { DiffAccount, ParsedTransaction } from "./types";

function makeTxn(
  overrides: Partial<ParsedTransaction> = {},
): ParsedTransaction {
  return {
    date: "08.05.2026",
    amount: 12.5,
    payee: "Lidl",
    comment: "",
    isIncome: false,
    categoryIds: [],
    ...overrides,
  };
}

const ACCOUNT: DiffAccount = {
  id: "107a4398-02ad-47d0-8102-c36faab9bf8a",
  changed: 0,
  user: 42,
  instrument: 3,
  type: "checking",
  title: "Caixa",
  archive: false,
};

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

describe("ensureIds", () => {
  test("assigns a UUID to a transaction missing an id and reports change", () => {
    const txns = [makeTxn()];
    const changed = ensureIds(txns);
    expect(changed).toBe(true);
    expect(txns[0].id).toMatch(UUID_RE);
  });

  test("preserves an existing id and reports no change", () => {
    const txns = [makeTxn({ id: "existing-id-123" })];
    const changed = ensureIds(txns);
    expect(changed).toBe(false);
    expect(txns[0].id).toBe("existing-id-123");
  });

  test("only fills missing ids in a mixed list", () => {
    const txns = [makeTxn({ id: "keep-me" }), makeTxn()];
    const changed = ensureIds(txns);
    expect(changed).toBe(true);
    expect(txns[0].id).toBe("keep-me");
    expect(txns[1].id).toMatch(UUID_RE);
  });

  test("assigns distinct ids to multiple unidentified transactions", () => {
    const txns = [makeTxn(), makeTxn()];
    ensureIds(txns);
    expect(txns[0].id).not.toBe(txns[1].id);
  });
});

describe("toDiffTransactions id handling", () => {
  test("reuses the transaction's existing id verbatim", () => {
    const txns = [makeTxn({ id: "stable-uuid-abc" })];
    const out = toDiffTransactions(txns, ACCOUNT);
    expect(out[0].id).toBe("stable-uuid-abc");
  });

  test("same input id yields the same output id across calls (idempotent)", () => {
    const txns = [makeTxn({ id: "stable-uuid-abc" })];
    const a = toDiffTransactions(txns, ACCOUNT);
    const b = toDiffTransactions(txns, ACCOUNT);
    expect(a[0].id).toBe(b[0].id);
  });

  test("generates a UUID when the transaction has no id", () => {
    const out = toDiffTransactions([makeTxn()], ACCOUNT);
    expect(out[0].id).toMatch(UUID_RE);
  });
});
