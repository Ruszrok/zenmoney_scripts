import type { ParsedTransaction } from "./types";

/**
 * Assign a stable UUID to every transaction that lacks one, mutating in place.
 * Returns true if any id was added (so the caller knows to persist the change).
 *
 * Stable ids are the basis of idempotent submission: the Diff API upserts by
 * transaction id, so reusing the same id on a re-submit updates the existing
 * record instead of creating a duplicate.
 */
export function ensureIds(txns: ParsedTransaction[]): boolean {
  let changed = false;
  for (const t of txns) {
    if (!t.id) {
      t.id = crypto.randomUUID();
      changed = true;
    }
  }
  return changed;
}
