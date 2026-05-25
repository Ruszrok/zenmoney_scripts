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
