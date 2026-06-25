import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { fetchAccounts, fetchTagGroups, submitTransactions } from "./api";
import {
  fetchAccountsDiff,
  fetchTagsDiff,
  submitTransactionsDiff,
} from "./diff-api";
import { ensureIds } from "./idempotency";
import type { ParsedTransaction, ReviewFile } from "./types";
import { ValidationError, validateTransactions } from "./validate";

const DATA_DIR = join(import.meta.dir, "..", "data");
const REVIEW_FILE = join(DATA_DIR, "review.json");

function usage(): never {
  console.error(`Usage:
  Auth (pick one): --token "<bearer>"  (or ZENMONEY_TOKEN env)  → official Diff API
                   --cookie "PHPSESSID=..."                      → legacy zenmoney.ru API

  bun run src/submit.ts --list-accounts   [--token <t> | --cookie <c>]
  bun run src/submit.ts --list-categories [--token <t> | --cookie <c>]
  bun run src/submit.ts --prepare --account "ID" [--token <t> | --cookie <c>] <<< '[json]'
  bun run src/submit.ts --submit-review   [--token <t> | --cookie <c>]
  bun run src/submit.ts [--dry-run] --account "ID" [--token <t> | --cookie <c>] <<< '[json]'

  Diff-API account IDs are UUIDs; legacy account IDs are integers.`);
  process.exit(1);
}

function parseArgs(args: string[]) {
  const flags = {
    cookie: "",
    token: "",
    account: "",
    listAccounts: false,
    listCategories: false,
    dryRun: false,
    prepare: false,
    submitReview: false,
  };
  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case "--cookie":
        flags.cookie = args[++i] ?? "";
        break;
      case "--token":
        flags.token = args[++i] ?? "";
        break;
      case "--account":
        flags.account = args[++i] ?? "";
        break;
      case "--list-accounts":
        flags.listAccounts = true;
        break;
      case "--list-categories":
        flags.listCategories = true;
        break;
      case "--dry-run":
        flags.dryRun = true;
        break;
      case "--prepare":
        flags.prepare = true;
        break;
      case "--submit-review":
        flags.submitReview = true;
        break;
    }
  }
  if (!flags.token) flags.token = process.env.ZENMONEY_TOKEN ?? "";
  return flags;
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of Bun.stdin.stream()) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf-8");
}

/** Read a JSON transaction array from stdin; exits with a clear error if empty/invalid. */
async function readParsedStdin(): Promise<ParsedTransaction[]> {
  const input = await readStdin();
  if (!input.trim()) {
    console.error("Error: no JSON input on stdin");
    usage();
  }
  try {
    return JSON.parse(input);
  } catch {
    console.error("Error: invalid JSON input");
    process.exit(1);
  }
}

function logSubmitResult(result: unknown): void {
  const count = Array.isArray(result) ? result.length : undefined;
  console.log(count != null ? `Created ${count} transaction(s).` : "Done.");
  console.log(JSON.stringify(result, null, 2));
}

function printValidationError(err: ValidationError): void {
  console.error(err.message);
  for (const issue of err.issues) {
    const where = issue.txnIndex === null ? "batch" : `txn[${issue.txnIndex}]`;
    console.error(
      `  ${where} ${issue.field}=${JSON.stringify(issue.value)}: ${issue.reason}`,
    );
  }
}

interface AccountRow {
  id: string;
  title: string;
  type: string;
  balance: number;
}
interface CategoryRow {
  id: string | number;
  label: string;
  type: string;
}

async function main() {
  const flags = parseArgs(process.argv.slice(2));

  // Token takes precedence over cookie; need at least one.
  const useDiff = !!flags.token;
  if (!useDiff && !flags.cookie) usage();
  console.error(
    useDiff
      ? "[auth] Diff API (api.zenmoney.ru) via bearer token"
      : "[auth] legacy zenmoney.ru API via PHPSESSID cookie",
  );

  // ── backend-neutral helpers ────────────────────────────────────────────
  const loadAccounts = async (): Promise<AccountRow[]> => {
    if (useDiff) return fetchAccountsDiff(flags.token);
    const accts = await fetchAccounts(flags.cookie);
    return Object.entries(accts).map(([id, a]) => ({
      id,
      title: a.title,
      type: a.type,
      balance: a.balance,
    }));
  };
  const loadCategories = async (): Promise<CategoryRow[]> =>
    useDiff ? fetchTagsDiff(flags.token) : fetchTagGroups(flags.cookie);
  const doSubmit = (txns: ParsedTransaction[], accountId: string) =>
    useDiff
      ? submitTransactionsDiff(flags.token, txns, accountId)
      : submitTransactions(flags.cookie, txns, accountId);
  // Fetch live accounts + categories and validate against them, returning both
  // so callers can reuse the categories (e.g. to embed in the review file).
  const validateAgainstLive = async (
    txns: ParsedTransaction[],
  ): Promise<{ accounts: AccountRow[]; categories: CategoryRow[] }> => {
    const accounts = await loadAccounts();
    const categories = await loadCategories();
    validateTransactions(
      txns,
      flags.account,
      new Set(accounts.map((a) => a.id)),
      new Set(categories.map((c) => String(c.id))),
    );
    return { accounts, categories };
  };

  if (flags.listAccounts) {
    for (const acc of await loadAccounts()) {
      console.log(
        `${acc.id}\t${acc.title}\t${acc.type}\tbalance: ${acc.balance}`,
      );
    }
    return;
  }

  if (flags.listCategories) {
    for (const c of await loadCategories()) {
      console.log(`${c.id}\t${c.label}\t${c.type}`);
    }
    return;
  }

  // --prepare: fetch live accounts + categories, validate, write review JSON.
  if (flags.prepare) {
    if (!flags.account) {
      console.error("Error: --account is required for --prepare");
      usage();
    }

    const parsed = await readParsedStdin();
    const { categories } = await validateAgainstLive(parsed);

    // Assign stable ids so a later --submit-review (or an accidental re-run)
    // upserts by id instead of creating duplicates.
    ensureIds(parsed);

    const review: ReviewFile = {
      account: flags.account,
      categories,
      transactions: parsed,
    };

    mkdirSync(DATA_DIR, { recursive: true });
    await Bun.write(REVIEW_FILE, `${JSON.stringify(review, null, 2)}\n`);
    console.log(`Review file written to ${REVIEW_FILE}`);
    console.log(
      `${parsed.length} transaction(s), ${categories.length} categories`,
    );
    return;
  }

  // --submit-review: read review JSON and submit (validator runs again inside
  // the backend with a fresh fetch — covers post-prepare edits).
  if (flags.submitReview) {
    const file = Bun.file(REVIEW_FILE);
    if (!(await file.exists())) {
      console.error(`Error: review file not found at ${REVIEW_FILE}`);
      console.error("Run --prepare first to generate it.");
      process.exit(1);
    }

    const review: ReviewFile = await file.json();

    // Backfill ids for any hand-added rows and persist before submitting, so
    // the ids we send are recorded and re-submits stay idempotent.
    if (ensureIds(review.transactions)) {
      await Bun.write(REVIEW_FILE, `${JSON.stringify(review, null, 2)}\n`);
    }

    console.log(`Submitting ${review.transactions.length} transaction(s)...`);
    const result = await doSubmit(review.transactions, review.account);
    logSubmitResult(result);
    return;
  }

  // Direct submit from stdin (legacy convenience path).
  if (!flags.account) {
    console.error("Error: --account is required for submitting transactions");
    usage();
  }

  const parsed = await readParsedStdin();

  if (flags.dryRun) {
    // Dry-run still validates so the user sees the same errors they would on submit.
    await validateAgainstLive(parsed);
    console.log("=== DRY RUN — validated, not submitting ===");
    console.log(JSON.stringify(parsed, null, 2));
    return;
  }

  console.log(`Submitting ${parsed.length} transaction(s)...`);
  const result = await doSubmit(parsed, flags.account);
  logSubmitResult(result);
}

main().catch((err) => {
  if (err instanceof ValidationError) {
    printValidationError(err);
    process.exit(2);
  }
  console.error("Error:", err.message);
  process.exit(1);
});
