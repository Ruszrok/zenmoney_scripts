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
