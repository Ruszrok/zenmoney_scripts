/** Default ZenMoney account ID (legacy cookie API). Set after --list-accounts. */
export const DEFAULT_ACCOUNT_ID = "11025256"; // (EUR) Bunq

/**
 * Default ZenMoney account ID for the Diff API (token mode). This is a UUID,
 * not the legacy integer above. Discover it with `--list-accounts --token ...`
 * and paste the (EUR) Bunq UUID here.
 */
export const DEFAULT_ACCOUNT_UUID = "e30b1cf6-0c08-430d-9a10-c7482d8948f1"; // (EUR) Bunq

/**
 * Category hint overrides: payee substring → tag_group ID.
 * Used when Claude's auto-categorization gets it wrong.
 * Example: { "Lidl": 650871 }
 */
export const CATEGORY_HINTS: Record<string, number> = {};
