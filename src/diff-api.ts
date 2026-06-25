import type {
  DiffAccount,
  DiffInstrument,
  DiffRequest,
  DiffResponse,
  DiffTag,
  DiffTransaction,
  ParsedTransaction,
} from "./types";
import { validateTransactions } from "./validate";

const BASE_URL = "https://api.zenmoney.ru";

function authHeaders(token: string) {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

/** Convert DD.MM.YYYY → yyyy-MM-dd (the date format the Diff API expects). */
export function toIsoDate(ddmmyyyy: string): string {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(ddmmyyyy);
  if (!m) throw new Error(`toIsoDate: expected DD.MM.YYYY, got "${ddmmyyyy}"`);
  return `${m[3]}-${m[2]}-${m[1]}`;
}

/** Low-level POST /v8/diff/. Throws on non-2xx (401 = token missing/expired). */
async function diff(token: string, body: DiffRequest): Promise<DiffResponse> {
  const res = await fetch(`${BASE_URL}/v8/diff/`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    if (res.status === 401) {
      throw new Error(
        `Diff API 401 Unauthorized — ZENMONEY_TOKEN is missing, invalid, or expired. ` +
          `Get a fresh token from budgera.com/settings/api-key or zerro.app/token.`,
      );
    }
    throw new Error(`Diff API failed: ${res.status} ${text}`);
  }
  return res.json();
}

export interface ServerData {
  serverTimestamp: number;
  accounts: DiffAccount[];
  tags: DiffTag[];
  instruments: DiffInstrument[];
  transactions: DiffTransaction[];
  userId: number | null;
}

/** One full read diff (serverTimestamp:0) → the entities we care about. */
export async function fetchServerData(token: string): Promise<ServerData> {
  const data = await diff(token, {
    currentClientTimestamp: nowSeconds(),
    serverTimestamp: 0,
  });
  const accounts = data.account ?? [];
  const userId = data.user?.[0]?.id ?? accounts[0]?.user ?? null;
  return {
    serverTimestamp: data.serverTimestamp,
    accounts,
    tags: data.tag ?? [],
    instruments: data.instrument ?? [],
    transactions: data.transaction ?? [],
    userId,
  };
}

export interface AccountSummary {
  id: string;
  title: string;
  type: string;
  instrument: number | null;
  balance: number;
  archive: boolean;
}

/** Normalized account list (UUID ids), parallel to legacy fetchAccounts. */
export async function fetchAccountsDiff(
  token: string,
): Promise<AccountSummary[]> {
  const { accounts } = await fetchServerData(token);
  return accounts
    .map((a) => ({
      id: a.id,
      title: a.title,
      type: a.type,
      instrument: a.instrument ?? null,
      balance: a.balance ?? 0,
      archive: a.archive,
    }))
    .sort((x, y) => x.title.localeCompare(y.title, "ru"));
}

export interface CategorySummary {
  id: string; // tag UUID
  label: string;
  type: string;
}

/**
 * Normalized category list (tag UUIDs), parallel to legacy fetchTagGroups.
 * label = "<parent title> / <title>" when nested, else "<title>".
 */
export async function fetchTagsDiff(token: string): Promise<CategorySummary[]> {
  const { tags } = await fetchServerData(token);
  const byId = new Map(tags.map((t) => [t.id, t]));
  const results = tags.map((t) => {
    const parent = t.parent ? byId.get(t.parent) : undefined;
    const label = parent ? `${parent.title} / ${t.title}` : t.title;
    const type = t.showOutcome ? "expense" : t.showIncome ? "income" : "hidden";
    return { id: t.id, label, type };
  });
  results.sort((a, b) => a.label.localeCompare(b.label, "ru"));
  return results;
}

/** Build Diff-API transaction objects from parsed transactions. */
export function toDiffTransactions(
  parsed: ParsedTransaction[],
  account: DiffAccount,
): DiffTransaction[] {
  const ts = nowSeconds();
  const instrument = account.instrument;
  if (instrument == null) {
    throw new Error(
      `account ${account.id} has no instrument (currency) — cannot build transactions`,
    );
  }
  return parsed.map((t) => {
    const tags = (t.categoryIds ?? []).map(String);
    // The Diff API validates the FULL object — every property below must be
    // present (nulls allowed). Key set mirrors what the server returns.
    return {
      // Reuse a stable client id when present so re-submits upsert instead of
      // duplicating; fall back to a fresh UUID for the direct-stdin path.
      id: t.id ?? crypto.randomUUID(),
      changed: ts,
      created: ts,
      user: account.user,
      deleted: false,
      hold: null,
      incomeInstrument: instrument,
      incomeAccount: account.id,
      income: t.isIncome ? t.amount : 0,
      incomeBankID: null,
      outcomeInstrument: instrument,
      outcomeAccount: account.id,
      outcome: t.isIncome ? 0 : t.amount,
      outcomeBankID: null,
      tag: tags.length ? tags : null,
      merchant: null,
      payee: t.payee,
      originalPayee: null,
      comment: t.comment || null,
      date: toIsoDate(t.date),
      reminderMarker: null,
      opIncome: null,
      opIncomeInstrument: null,
      opOutcome: null,
      opOutcomeInstrument: null,
      latitude: null,
      longitude: null,
      qrCode: null,
      source: null,
      viewed: true,
    };
  });
}

/**
 * Submits parsed transactions for the given account UUID via the Diff API.
 * ALWAYS validates first against the live accounts + tags for the token —
 * mirrors the legacy submitTransactions contract.
 *
 * Returns the transaction objects echoed back by the server.
 */
export async function submitTransactionsDiff(
  token: string,
  transactions: ParsedTransaction[],
  accountId: string,
): Promise<DiffTransaction[]> {
  const data = await fetchServerData(token);

  validateTransactions(
    transactions,
    accountId,
    new Set(data.accounts.map((a) => a.id)),
    new Set(data.tags.map((t) => t.id)),
  );

  const account = data.accounts.find((a) => a.id === accountId)!;
  const body = toDiffTransactions(transactions, account);
  const sentIds = new Set(body.map((t) => t.id));

  const res = await diff(token, {
    currentClientTimestamp: nowSeconds(),
    serverTimestamp: data.serverTimestamp,
    transaction: body,
  });

  // Server echoes objects changed since serverTimestamp; surface the ones we sent.
  return (res.transaction ?? []).filter((t) => sentIds.has(t.id));
}

/** Hard-delete a transaction by UUID (used by verification cleanup). */
export async function deleteTransactionDiff(
  token: string,
  id: string,
): Promise<void> {
  const data = await fetchServerData(token);
  const userId = data.userId;
  if (userId == null) throw new Error("could not resolve user id for deletion");
  await diff(token, {
    currentClientTimestamp: nowSeconds(),
    serverTimestamp: data.serverTimestamp,
    deletion: [
      { id, object: "transaction", stamp: nowSeconds(), user: userId },
    ],
  });
}
