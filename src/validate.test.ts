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
