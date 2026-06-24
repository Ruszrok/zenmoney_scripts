/** What Claude extracts from banking screenshots */
export interface ParsedTransaction {
  date: string; // DD.MM.YYYY
  amount: number;
  payee: string;
  comment: string;
  isIncome: boolean;
  /**
   * Category IDs. Legacy (cookie) mode uses integer tag_group IDs; Diff-API
   * (token) mode uses tag UUID strings. Empty = uncategorized.
   */
  categoryIds: (number | string)[];
  categoryName?: string; // human-readable, for review only — not sent to API
}

/** Review file saved to data/ for user approval before submission */
export interface ReviewFile {
  account: string;
  categories: { id: string | number; label: string; type: string }[];
  transactions: ParsedTransaction[];
}

// ───────────────────────── Legacy (zenmoney.ru cookie) API ─────────────────

/** ZenMoney API payload for creating a transaction (legacy /api/v2/transaction/) */
export interface ZenMoneyTransaction {
  category: string;
  tag_groups: string[];
  income: number;
  outcome: number;
  date: string; // DD.MM.YYYY
  comment: string;
  payee: string;
  account_income: string;
  account_outcome: string;
}

/** ZenMoney account from /api/v1/account/ */
export interface ZenMoneyAccount {
  id: number;
  title: string;
  type: string;
  balance: number;
  currency_id: number;
}

/** ZenMoney category from /api/v1/category/ */
export interface ZenMoneyCategory {
  id: number;
  title: string;
  parent_id: number | null;
  type: string;
}

// ───────────────────────── Official Diff API (api.zenmoney.ru/v8/diff/) ─────
// Field names are verbatim from the ZenMoney API wiki schema.

/** Currency. instrument.id is referenced by accounts/transactions as an Int. */
export interface DiffInstrument {
  id: number;
  changed: number;
  title: string;
  shortTitle: string; // 3-letter currency code, e.g. "EUR"
  symbol: string;
  rate: number;
}

export interface DiffAccount {
  id: string; // UUID
  changed: number;
  user: number; // User.id
  instrument: number | null; // Instrument.id
  type: string; // 'cash'|'ccard'|'checking'|'loan'|'deposit'|'emoney'|'debt'
  title: string;
  balance?: number;
  archive: boolean;
  // …other fields exist on the wire but are not needed here
}

export interface DiffTag {
  id: string; // UUID
  changed: number;
  user: number;
  title: string;
  parent: string | null; // Tag.id, max 1 level of nesting
  showIncome: boolean;
  showOutcome: boolean;
  // …other fields exist on the wire but are not needed here
}

export interface DiffTransaction {
  id: string; // UUID (client-generated on create)
  changed: number;
  created: number;
  user: number;
  deleted: boolean;
  hold?: boolean | null;
  incomeInstrument: number;
  incomeAccount: string; // Account.id (UUID)
  income: number; // >= 0
  outcomeInstrument: number;
  outcomeAccount: string; // Account.id (UUID)
  outcome: number; // >= 0
  incomeBankID?: string | null;
  outcomeBankID?: string | null;
  tag?: string[] | null; // Tag.id references
  merchant?: string | null;
  payee?: string | null;
  originalPayee?: string | null;
  comment?: string | null;
  date: string; // 'yyyy-MM-dd'
  reminderMarker?: string | null;
  opIncome?: number | null;
  opIncomeInstrument?: number | null;
  opOutcome?: number | null;
  opOutcomeInstrument?: number | null;
  latitude?: number | null;
  longitude?: number | null;
  qrCode?: string | null;
  source?: string | null;
  viewed?: boolean;
}

/** One object's deletion record sent back in a write diff. */
export interface DiffDeletion {
  id: string;
  object: string; // entity class, e.g. "transaction"
  stamp: number; // unix seconds
  user: number;
}

/** Request body for POST /v8/diff/ */
export interface DiffRequest {
  currentClientTimestamp: number; // unix seconds
  serverTimestamp: number; // 0 = full fetch
  transaction?: DiffTransaction[];
  deletion?: DiffDeletion[];
  forceFetch?: string[];
}

/** Response body from POST /v8/diff/ (only the parts we read). */
export interface DiffResponse {
  serverTimestamp: number;
  instrument?: DiffInstrument[];
  account?: DiffAccount[];
  tag?: DiffTag[];
  transaction?: DiffTransaction[];
  user?: { id: number }[];
  deletion?: DiffDeletion[];
}
