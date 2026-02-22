/** Default ZenMoney account ID. Set after running --list-accounts. */
export const DEFAULT_ACCOUNT_ID = "11025256"; // (EUR) Bunq

/**
 * Category hint overrides: payee substring → tag_group ID.
 * Used when Claude's auto-categorization gets it wrong.
 * Example: { "Lidl": 650871 }
 */
export const CATEGORY_HINTS: Record<string, number> = {};
