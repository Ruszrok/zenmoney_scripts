import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { submitTransactions } from "./api";
import type { ParsedTransaction } from "./types";
import { ValidationError } from "./validate";

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
      typeof init?.body === "string"
        ? init.body
        : init?.body == null
          ? null
          : String(init.body);
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
  [ACCOUNT]: {
    id: Number(ACCOUNT),
    title: "Bunq",
    type: "ccard",
    balance: 0,
    currency_id: 1,
  },
  "11025255": {
    id: 11025255,
    title: "Caixa",
    type: "ccard",
    balance: 0,
    currency_id: 1,
  },
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

    expect(calls[2].headers.cookie).toBe(COOKIE);
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
      await submitTransactions(
        COOKIE,
        [txn({ categoryIds: [999999] })],
        ACCOUNT,
      );
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
