import type { ZenMoneyAccount, ZenMoneyCategory, ZenMoneyTransaction } from "./types";

const BASE_URL = "https://zenmoney.ru/api";

function headers(cookie: string) {
  return {
    Cookie: cookie,
    "X-Requested-With": "XMLHttpRequest",
  };
}

export async function fetchAccounts(
  cookie: string
): Promise<Record<string, ZenMoneyAccount>> {
  const res = await fetch(`${BASE_URL}/v1/account/`, {
    headers: headers(cookie),
  });
  if (!res.ok) throw new Error(`fetchAccounts failed: ${res.status}`);
  return res.json();
}

export async function fetchCategories(
  cookie: string
): Promise<Record<string, ZenMoneyCategory>> {
  const res = await fetch(`${BASE_URL}/v1/category/`, {
    headers: headers(cookie),
  });
  if (!res.ok) throw new Error(`fetchCategories failed: ${res.status}`);
  return res.json();
}

/** Fetch full tag_groups hierarchy (with subcategories) from /api/s1/profile/ */
export async function fetchTagGroups(
  cookie: string
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
    const type = g.show_outcome ? "expense" : g.show_income ? "income" : "hidden";
    results.push({ id: g.id, label, type });
  }
  results.sort((a, b) => a.label.localeCompare(b.label, "ru"));
  return results;
}

export async function submitTransactions(
  cookie: string,
  transactions: ZenMoneyTransaction[]
): Promise<unknown> {
  const res = await fetch(`${BASE_URL}/v2/transaction/`, {
    method: "POST",
    headers: {
      ...headers(cookie),
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: JSON.stringify(transactions),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`submitTransactions failed: ${res.status} ${text}`);
  }
  return res.json();
}
