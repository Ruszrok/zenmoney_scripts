import { describe, expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { ReviewFile } from "./types";
import { validateTransactions } from "./validate";

const REVIEW_PATH = join(import.meta.dir, "..", "data", "review.json");

describe("regression: data/review.json", () => {
  test("current review.json passes the new validator (offline)", () => {
    if (!existsSync(REVIEW_PATH)) {
      // No review file checked in — nothing to regress against.
      return;
    }
    const review: ReviewFile = JSON.parse(readFileSync(REVIEW_PATH, "utf-8"));
    const accountIds = new Set([review.account]);
    // Validator keys on String(id); categories may be integer (legacy) or UUID (Diff).
    const categoryIds = new Set(review.categories.map((c) => String(c.id)));

    expect(() =>
      validateTransactions(
        review.transactions,
        review.account,
        accountIds,
        categoryIds,
      ),
    ).not.toThrow();
  });
});
