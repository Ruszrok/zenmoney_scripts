/** What Claude extracts from banking screenshots */
export interface ParsedTransaction {
  date: string; // DD.MM.YYYY
  amount: number;
  payee: string;
  comment: string;
  isIncome: boolean;
  categoryId: number; // tag_group ID, 0 = uncategorized
}

/** ZenMoney API payload for creating a transaction */
export interface ZenMoneyTransaction {
  tag_groups: number[];
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
