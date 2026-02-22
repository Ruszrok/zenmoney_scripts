import { fetchAccounts, fetchCategories, submitTransactions } from "./api";
import type {
  ParsedTransaction,
  ReviewFile,
  ZenMoneyTransaction,
} from "./types";
import { mkdirSync } from "fs";
import { join } from "path";

const DATA_DIR = join(import.meta.dir, "..", "data");
const REVIEW_FILE = join(DATA_DIR, "review.json");

function usage(): never {
  console.error(`Usage:
  bun run src/submit.ts --list-accounts --cookie "PHPSESSID=..."
  bun run src/submit.ts --list-categories --cookie "PHPSESSID=..."
  bun run src/submit.ts --prepare --cookie "PHPSESSID=..." --account "ID" <<< '[json]'
  bun run src/submit.ts --submit-review --cookie "PHPSESSID=..."
  bun run src/submit.ts [--dry-run] --cookie "PHPSESSID=..." --account "ID" <<< '[json]'`);
  process.exit(1);
}

function parseArgs(args: string[]) {
  const flags = {
    cookie: "",
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
  return flags;
}

function toZenMoney(
  parsed: ParsedTransaction[],
  accountId: string
): ZenMoneyTransaction[] {
  return parsed.map((t) => ({
    tag_groups: t.categoryId ? [t.categoryId] : [],
    income: t.isIncome ? t.amount : 0,
    outcome: t.isIncome ? 0 : t.amount,
    date: t.date,
    comment: t.comment,
    payee: t.payee,
    account_income: accountId,
    account_outcome: accountId,
  }));
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of Bun.stdin.stream()) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf-8");
}

async function main() {
  const flags = parseArgs(process.argv.slice(2));

  if (!flags.cookie) usage();

  if (flags.listAccounts) {
    const accounts = await fetchAccounts(flags.cookie);
    for (const [id, acc] of Object.entries(accounts)) {
      console.log(`${id}\t${acc.title}\t${acc.type}\tbalance: ${acc.balance}`);
    }
    return;
  }

  if (flags.listCategories) {
    const categories = await fetchCategories(flags.cookie);
    for (const [id, cat] of Object.entries(categories)) {
      const parent = cat.parent_id ? ` (parent: ${cat.parent_id})` : "";
      console.log(`${id}\t${cat.title}${parent}\t${cat.type}`);
    }
    return;
  }

  // --prepare: fetch categories, merge with parsed transactions, write review JSON
  if (flags.prepare) {
    if (!flags.account) {
      console.error("Error: --account is required for --prepare");
      usage();
    }

    const input = await readStdin();
    if (!input.trim()) {
      console.error("Error: no JSON input on stdin");
      usage();
    }

    let parsed: ParsedTransaction[];
    try {
      parsed = JSON.parse(input);
    } catch {
      console.error("Error: invalid JSON input");
      process.exit(1);
    }

    const rawCategories = await fetchCategories(flags.cookie);
    const categories = Object.entries(rawCategories).map(([id, cat]) => ({
      id: Number(id),
      title: cat.title,
      type: cat.type,
    }));

    const review: ReviewFile = {
      account: flags.account,
      categories,
      transactions: parsed,
    };

    mkdirSync(DATA_DIR, { recursive: true });
    await Bun.write(REVIEW_FILE, JSON.stringify(review, null, 2) + "\n");
    console.log(`Review file written to ${REVIEW_FILE}`);
    console.log(
      `${parsed.length} transaction(s), ${categories.length} categories`
    );
    return;
  }

  // --submit-review: read review JSON and submit
  if (flags.submitReview) {
    const file = Bun.file(REVIEW_FILE);
    if (!(await file.exists())) {
      console.error(`Error: review file not found at ${REVIEW_FILE}`);
      console.error("Run --prepare first to generate it.");
      process.exit(1);
    }

    const review: ReviewFile = await file.json();
    const transactions = toZenMoney(review.transactions, review.account);

    console.log(`Submitting ${transactions.length} transaction(s)...`);
    const result = await submitTransactions(flags.cookie, transactions);
    console.log("Success:", JSON.stringify(result, null, 2));
    return;
  }

  // Direct submit from stdin (legacy)
  if (!flags.account) {
    console.error("Error: --account is required for submitting transactions");
    usage();
  }

  const input = await readStdin();
  if (!input.trim()) {
    console.error("Error: no JSON input on stdin");
    usage();
  }

  let parsed: ParsedTransaction[];
  try {
    parsed = JSON.parse(input);
  } catch {
    console.error("Error: invalid JSON input");
    process.exit(1);
  }

  const transactions = toZenMoney(parsed, flags.account);

  if (flags.dryRun) {
    console.log("=== DRY RUN — would submit: ===");
    console.log(JSON.stringify(transactions, null, 2));
    return;
  }

  console.log(`Submitting ${transactions.length} transaction(s)...`);
  const result = await submitTransactions(flags.cookie, transactions);
  console.log("Success:", JSON.stringify(result, null, 2));
}

main().catch((err) => {
  console.error("Error:", err.message);
  process.exit(1);
});
