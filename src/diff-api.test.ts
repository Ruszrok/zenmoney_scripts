import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import {
  deleteTransactionDiff,
  fetchTagsDiff,
  submitTransactionsDiff,
  toDiffTransactions,
  toIsoDate,
} from "./diff-api";
import type { DiffAccount, ParsedTransaction } from "./types";
import { ValidationError } from "./validate";

// ── fetch mock harness (mirrors api.test.ts) ────────────────────────────────
interface FetchCall {
  url: string;
  method: string;
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
    const body = typeof init?.body === "string" ? init.body : null;
    calls.push({ url, method, body });
    const next = queue.shift();
    if (!next) throw new Error(`unexpected fetch call to ${url} (${method})`);
    return next();
  }) as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

const TOKEN = "tok";
const ACCOUNT_UUID = "e30b1cf6-0c08-430d-9a10-c7482d8948f1";

const ACCOUNT: DiffAccount = {
  id: ACCOUNT_UUID,
  changed: 0,
  user: 7,
  instrument: 2,
  type: "ccard",
  title: "Bunq",
  archive: false,
};

// A full read-diff response (serverTimestamp:0 fetch).
const serverDiff = {
  serverTimestamp: 1000,
  account: [{ ...ACCOUNT, balance: 0 }],
  tag: [
    {
      id: "tag-food",
      changed: 1,
      user: 7,
      title: "Food",
      parent: null,
      showIncome: false,
      showOutcome: true,
    },
    {
      id: "tag-groceries",
      changed: 1,
      user: 7,
      title: "Groceries",
      parent: "tag-food",
      showIncome: false,
      showOutcome: true,
    },
  ],
  instrument: [
    {
      id: 2,
      changed: 1,
      title: "Euro",
      shortTitle: "EUR",
      symbol: "€",
      rate: 1,
    },
  ],
  transaction: [],
  user: [{ id: 7 }],
};

function txn(overrides: Partial<ParsedTransaction> = {}): ParsedTransaction {
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

describe("toIsoDate", () => {
  test("converts DD.MM.YYYY → yyyy-MM-dd preserving day/month order", () => {
    expect(toIsoDate("08.05.2026")).toBe("2026-05-08");
    expect(toIsoDate("31.12.1999")).toBe("1999-12-31");
  });

  test("throws on input that is not DD.MM.YYYY", () => {
    expect(() => toIsoDate("2026-05-08")).toThrow(/DD\.MM\.YYYY/);
    expect(() => toIsoDate("8.5.2026")).toThrow(/DD\.MM\.YYYY/);
  });
});

describe("toDiffTransactions", () => {
  test("expense: outcome = amount, income = 0, both legs on the account", () => {
    const [out] = toDiffTransactions(
      [txn({ isIncome: false, amount: 12.5 })],
      ACCOUNT,
    );
    expect(out.income).toBe(0);
    expect(out.outcome).toBe(12.5);
    expect(out.incomeAccount).toBe(ACCOUNT_UUID);
    expect(out.outcomeAccount).toBe(ACCOUNT_UUID);
    expect(out.incomeInstrument).toBe(2);
    expect(out.outcomeInstrument).toBe(2);
  });

  test("income: income = amount, outcome = 0", () => {
    const [out] = toDiffTransactions(
      [txn({ isIncome: true, amount: 30 })],
      ACCOUNT,
    );
    expect(out.income).toBe(30);
    expect(out.outcome).toBe(0);
  });

  test("empty categoryIds → tag null; non-empty → stringified array", () => {
    expect(
      toDiffTransactions([txn({ categoryIds: [] })], ACCOUNT)[0].tag,
    ).toBeNull();
    expect(
      toDiffTransactions(
        [txn({ categoryIds: ["tag-food", "tag-groceries"] })],
        ACCOUNT,
      )[0].tag,
    ).toEqual(["tag-food", "tag-groceries"]);
  });

  test("empty comment becomes null", () => {
    expect(
      toDiffTransactions([txn({ comment: "" })], ACCOUNT)[0].comment,
    ).toBeNull();
    expect(
      toDiffTransactions([txn({ comment: "x" })], ACCOUNT)[0].comment,
    ).toBe("x");
  });

  test("converts the date to ISO", () => {
    expect(
      toDiffTransactions([txn({ date: "08.05.2026" })], ACCOUNT)[0].date,
    ).toBe("2026-05-08");
  });

  test("emits every key the Diff API requires (nullable props present)", () => {
    const [out] = toDiffTransactions([txn()], ACCOUNT);
    const required = [
      "id",
      "changed",
      "created",
      "user",
      "deleted",
      "hold",
      "incomeInstrument",
      "incomeAccount",
      "income",
      "incomeBankID",
      "outcomeInstrument",
      "outcomeAccount",
      "outcome",
      "outcomeBankID",
      "tag",
      "merchant",
      "payee",
      "originalPayee",
      "comment",
      "date",
      "reminderMarker",
      "opIncome",
      "opIncomeInstrument",
      "opOutcome",
      "opOutcomeInstrument",
      "latitude",
      "longitude",
      "qrCode",
      "source",
      "viewed",
    ];
    for (const key of required) expect(out).toHaveProperty(key);
  });

  test("throws when the account has no instrument", () => {
    expect(() =>
      toDiffTransactions([txn()], { ...ACCOUNT, instrument: null }),
    ).toThrow(/no instrument/);
  });
});

describe("fetchTagsDiff", () => {
  test("builds nested labels, derives expense type, sorts by label", async () => {
    queue.push(() => jsonResponse(serverDiff));
    const cats = await fetchTagsDiff(TOKEN);
    const byId = new Map(cats.map((c) => [c.id, c]));
    expect(byId.get("tag-food")?.label).toBe("Food");
    expect(byId.get("tag-groceries")?.label).toBe("Food / Groceries");
    expect(byId.get("tag-food")?.type).toBe("expense");
    const labels = cats.map((c) => c.label);
    expect(labels).toEqual(
      [...labels].sort((a, b) => a.localeCompare(b, "ru")),
    );
    expect(JSON.parse(calls[0].body ?? "{}").serverTimestamp).toBe(0);
  });

  test("derives income / hidden from the show flags", async () => {
    queue.push(() =>
      jsonResponse({
        ...serverDiff,
        tag: [
          {
            id: "t-inc",
            changed: 1,
            user: 7,
            title: "Salary",
            parent: null,
            showIncome: true,
            showOutcome: false,
          },
          {
            id: "t-hid",
            changed: 1,
            user: 7,
            title: "Hidden",
            parent: null,
            showIncome: false,
            showOutcome: false,
          },
        ],
      }),
    );
    const cats = await fetchTagsDiff(TOKEN);
    const byId = new Map(cats.map((c) => [c.id, c]));
    expect(byId.get("t-inc")?.type).toBe("income");
    expect(byId.get("t-hid")?.type).toBe("hidden");
  });
});

describe("submitTransactionsDiff", () => {
  test("validates, POSTs the built body, and returns only the txns we sent", async () => {
    queue.push(() => jsonResponse(serverDiff)); // read diff
    queue.push(() =>
      jsonResponse({
        serverTimestamp: 1001,
        // server echoes EVERYTHING changed since serverTimestamp — ours + a foreign row
        transaction: [
          { id: "mine", income: 0, outcome: 12.5 },
          { id: "other-unrelated", income: 0, outcome: 99 },
        ],
      }),
    );

    const result = await submitTransactionsDiff(
      TOKEN,
      [txn({ id: "mine", categoryIds: ["tag-food"] })],
      ACCOUNT_UUID,
    );

    // the foreign echoed row is filtered out by sentIds
    expect(result.map((t) => t.id)).toEqual(["mine"]);

    expect(calls).toHaveLength(2);
    const writeBody = JSON.parse(calls[1].body ?? "{}");
    expect(writeBody.serverTimestamp).toBe(1000); // reuses the read's timestamp
    expect(writeBody.transaction).toHaveLength(1);
    expect(writeBody.transaction[0]).toMatchObject({
      id: "mine",
      income: 0,
      outcome: 12.5,
      incomeAccount: ACCOUNT_UUID,
      tag: ["tag-food"],
    });
  });

  test("throws ValidationError on an unknown account before the write POST", async () => {
    queue.push(() => jsonResponse(serverDiff));
    let err: unknown;
    try {
      await submitTransactionsDiff(TOKEN, [txn()], "no-such-account");
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    expect(calls).toHaveLength(1); // read only, no write diff issued
  });

  test("throws ValidationError on a foreign categoryId before the write POST", async () => {
    queue.push(() => jsonResponse(serverDiff));
    let err: unknown;
    try {
      await submitTransactionsDiff(
        TOKEN,
        [txn({ categoryIds: ["nope"] })],
        ACCOUNT_UUID,
      );
    } catch (e) {
      err = e;
    }
    expect(err).toBeInstanceOf(ValidationError);
    expect(calls).toHaveLength(1);
  });
});

describe("deleteTransactionDiff", () => {
  test("resolves the user id and sends a deletion record", async () => {
    queue.push(() => jsonResponse(serverDiff)); // read (user:[{id:7}])
    queue.push(() => jsonResponse({ serverTimestamp: 1002 })); // write ack
    await deleteTransactionDiff(TOKEN, "txn-123");
    expect(calls).toHaveLength(2);
    const body = JSON.parse(calls[1].body ?? "{}");
    expect(body.deletion).toHaveLength(1);
    expect(body.deletion[0]).toMatchObject({
      id: "txn-123",
      object: "transaction",
      user: 7,
    });
  });

  test("throws when the user id cannot be resolved", async () => {
    queue.push(() =>
      jsonResponse({
        serverTimestamp: 1000,
        account: [],
        tag: [],
        transaction: [],
      }),
    );
    let err: Error | undefined;
    try {
      await deleteTransactionDiff(TOKEN, "txn-123");
    } catch (e) {
      err = e as Error;
    }
    expect(err?.message).toMatch(/could not resolve user id/);
  });
});
