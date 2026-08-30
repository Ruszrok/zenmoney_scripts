# ZenMoney Transaction Automation

This project automates adding bank transactions to ZenMoney from banking app screenshots.

There are **two auth backends**, selected automatically by which credential you pass:

- **Diff API (token) — PRIMARY.** Official API at `https://api.zenmoney.ru/v8/diff/` with a bearer token. Stable, doesn't log out. Account & category IDs are **UUIDs**.
- **Legacy (cookie) — fallback.** Private web API at `https://zenmoney.ru/api/...` with a `PHPSESSID` cookie. Account & category IDs are **integers**. The cookie expires/logs out constantly — only use if you have no token.

`src/submit.ts` uses the token (`--token` or `ZENMONEY_TOKEN` env) when present, otherwise the cookie (`--cookie`).

## Workflow

When the user attaches banking screenshots, follow these steps:

### 1. Parse screenshots

Use vision to extract transactions from each screenshot. For each transaction, extract:
- **date** — format DD.MM.YYYY
- **amount** — numeric, always positive
- **payee** — merchant/sender name (ASCII only — strip diacritics & `&`, they break iOS sync)
- **comment** — additional details (can be empty)
- **isIncome** — true if money received, false if spent
- Skip **Denied** / **pending** transactions, and self-transfers (e.g. "Viacheslav Iudanov" payments).

**bunq status vocabulary** (they are not interchangeable — read the subtitle *and* the amount styling):

| Screenshot cue | Meaning | Action |
|---|---|---|
| `Denied …` + struck-through amount | never went through | **skip** |
| struck-through amount, any subtitle (incl. `Payment returned`) | reversed / voided | **skip** |
| `Payment pending` | authorization not yet settled; settles later as its **own separate row** | **skip** — else it double-counts |
| `Reserved … payment` | authorized, settles **in place** (no second row) | **include** — user confirmed these count as paid |
| `Refund` | real inbound money | include as `isIncome: true` |

Dates: `Today` / `Yesterday` headers resolve against the screenshot's clock, not today's date — check the status-bar time.

### 1b. Check for duplicates BEFORE preparing

Screenshots overlap heavily and a range may already be partly imported. Always run a dedup pass first — the user asks for this. Read-only, via `fetchServerData`:

1. Pull server transactions for the target account, find the **latest existing date**, and drop every parsed row at/before it.
2. After `--prepare`, verify three things against the server: (a) no identical `date|amount|payee|isIncome` rows *within* `review.json` (catches double-entry from overlapping screenshots), (b) no `date|amount` match already on the account, (c) no `date|amount` match on **any other account** (catches a row filed to the wrong account).
3. Repeated payees are normal, not dupes — e.g. Acento Coffee ×3 same day at different amounts, weekly `bunq Payday` €14.62. Only flag *exact* full-key matches.

### 2. Auth — get a token (primary)

Get a ZenMoney bearer token (one-time) and put it in a gitignored `.env`:

```bash
echo 'ZENMONEY_TOKEN=...' > .env
```

Copy the token from **`budgera.com/settings/api-key`** (the "API Key" page — it's a permanent token) or **`zerro.app/token`**. Bun auto-loads `.env`, so every `bun run src/submit.ts ...` picks it up — no flag needed. The token is a standard ZenMoney OAuth bearer; at runtime only `api.zenmoney.ru` is contacted (no dependency on Budgera/Zerro). The browser tool **blocks** reading the raw token, so the user must paste it themselves.

> **Legacy cookie mode:** instead of a token, run `document.cookie` via `mcp__claude-in-chrome__javascript_tool` on a logged-in zenmoney.ru tab, extract `PHPSESSID=...`, and pass `--cookie "PHPSESSID=xxx"` to every command. Everything below works identically; just swap the auth flag and use integer IDs.

### 3. Fetch categories

```bash
bun run src/submit.ts --list-categories
```

Returns the full category hierarchy as `id<TAB>label<TAB>type` (e.g. "Проезд / Такси", "Еда / Продукты"). In token mode `id` is a **tag UUID**; in cookie mode it's an integer tag_group ID.

### 4. Categorize transactions

Map each transaction to one or more category IDs. Transactions use `categoryIds` (array) — multiple categories can be applied to one transaction. In token mode these are **UUID strings**; in cookie mode **integers**.

**Important:** category IDs are per-user AND per-auth-mode. The hints table below is a *cache* of this user's **Diff-API tag UUIDs**. Every `--prepare` and `--submit-review` validates `categoryIds` against the live categories for the session and **hard-fails** (exit 2) on any foreign ID. If you see a `ValidationError`, rebuild your choices from the `categories` array in the freshly-written `data/review.json` and update this table.

**Category mapping hints** (payee substring → category → Diff-API tag UUID):
| Payee contains | Category | tag UUID |
|---|---|---|
| Bolt, Uber, Yandex Go, Lime, Donkey Republic, Rejsekort, Copenhagen Metro, Aeroporto | Проезд / Такси | `990e0f14-98d2-4ebb-876d-79e0c32d6bb4` |
| Uber Eats, Yandex Eats, Wolt, Glovo, Starbucks, McDonalds, BEEBEE, Monica Empire III, Acento Coffee, Drop Specialty Coffee, Zahir Kebab, ULU Cascais, Tertulia Apelativa, Alba bistro, Artisani, TACT, SumUp Artes e oficio, restaurants/cafes/coffee | Еда / Кафе и рестораны | `4789c9cc-698f-432e-a4e2-ab2edee8fdcb` |
| Lidl, Continente, Pingo Doce, Gleba, Carrefour, Auchan, Maxima, Rimi, Netto, fotex, CAFEMILAGRE.COM, MIX Markt, Minipreco | Еда / Продукты | `e01738fa-7b33-449f-aca7-8bf6474b23a0` |
| Decathlon, Protennis, PayPal Tennispro, BSPORT, Rackets Pro Academy, FEDERACAO PORT | Спорт | `3d5bb1c5-f876-4b0c-a257-a4634f4e41e3` |
| Farmacia, Pharm Center, Dental, medical, HOSPITAL CUF, WELLS.PT | Медицицина | `3f8cca35-e2dc-4019-ae47-c666ff2d802c` |
| Spotify, Netflix, YouTube, HBO Max, Zoom, LinkedIn, Paddle, OpenAI, Claude, Anthropic, Apple (small) | Подписки | `81a86b5e-8f1b-41e6-9bd2-85287b9c2fae` |
| Fly.io, Lovable, Cloudflare, Figma, Google Cloud | Бизнес расходы / 4realty.ai | `f4595a1e-2bf8-41ec-a349-ed8f900ec5f0` |
| Maksu Services SA, Bx Valor 03-transacco, GALP, TMP (parking), IUC, **A.S. \<name\>** (Area de Servico = fuel station, e.g. A.S. TELHEIRAS) | Машина | `ba9368a2-78f6-4ba2-99f8-13e9a06650ad` |
| GOLDENERGY, PAGAMENTOS VOD, LMW*EDP, LW*EDP, PQ EDF SEDE EDP | Квартплата | `cc444313-43ad-4fdb-8930-61076935ea6c` |
| Amazon, AliExpress, UNIQLO, Duty Free, Magasin, Other Stories, Polo 1921, AS Originals Colombo | Личные траты | `24ed7b3f-4bfc-4324-88f3-42498c1f3d7c` |
| G2A (game keys) | Отдых и развлечения | `a7555d2c-c1ad-4e44-a999-28e9720bb2c9` |
| PayPal (bare, no sub-merchant) | Хобби | `3b234c7f-467c-4919-877e-928a5a7a34f6` |
| Regus, coworking/office space | Бизнес расходы (generic) | `a014ce97-53d5-4163-afeb-51664471bbd6` |
| Holiday Inn, Marriott, Air France, Paris Aeroport, hotels/flights | Отпуск | `f05417a6-dc01-44dc-a2e7-abf0fcceae48` |
| SEF, INSTITUTO DE GESTAO F | Налоги и пошлины | `365101f2-302f-4fbc-9eef-91747b51ce25` |
| MANUT CONTA, Invoice (bunq) | Банковские издержки | `fd46abc5-0ec3-4767-84d2-0479bc7bde2a` |
| Salary, Palk, Deel | Зарплата (income) | `4869a1f1-a06f-4456-9bc5-fd3e5de6f051` |
| Cashback, bunq Payday, bunq "Payment received" | проценты (income) | `c14c0c71-1859-44f1-9451-03b1ba4b6f1c` |
| Swedbank, SEB, Luminor, Revolut | skip — likely a transfer |  |
| To EUR, TRF CXDAPP, TRF POUP, Trf Mbway, LEVANTAMENTO | transfer — add with comment, user converts manually |  |

When unsure, set `categoryIds: []` (uncategorized) and note it in the confirmation table.

### 5. Save review file

Pipe the parsed transactions JSON to `--prepare`, which fetches live categories and writes `data/review.json`:

```bash
bun run src/submit.ts --prepare --account "ACCOUNT_ID" <<< '[
  {"date":"22.02.2026","amount":45.50,"payee":"Lidl","comment":"","isIncome":false,"categoryIds":["e01738fa-7b33-449f-aca7-8bf6474b23a0"],"categoryName":"Еда / Продукты"}
]'
```

`ACCOUNT_ID` is the bunq UUID `e30b1cf6-0c08-430d-9a10-c7482d8948f1` in token mode, or `11025256` in cookie mode (both are also recorded in MEMORY.md).

The review file contains:
- `categories` — all available categories (id, label, type) with full hierarchy
- `transactions` — each transaction with `categoryIds` and `categoryName` for easy review
- `account` — the target account ID

**Always include `categoryName`** in each transaction — it's for user review only and is ignored during submission.

### 6. User reviews `data/review.json`

Wait for the user to review and edit the file. They may change `categoryIds`/`categoryName` or remove transactions.

### 7. Submit from review file

```bash
bun run src/submit.ts --submit-review
```

This reads `data/review.json` and submits all transactions in it. The submit path validates a second time inside the backend (`submitTransactionsDiff` in `src/diff-api.ts`, or `submitTransactions` in `src/api.ts`) using a fresh fetch of accounts + categories, so post-`--prepare` edits are still checked. No flag disables this. On validation failure the CLI prints all issues and exits with code 2.

## Other commands

```bash
bun run src/submit.ts --list-accounts          # find account ID (UUID in token mode)
bun run src/submit.ts --dry-run --account ID <<< '[json]'   # validate without submitting
```

### Creating a new category (tag)

There is **no CLI flag** for this — the user sometimes asks ("создай её в отпусках если её нет"). Hand-roll a `POST /v8/diff/` with a `tag: [ … ]` body. The server needs the **full** tag shape and a client-generated **v4 UUID**; copy the field set from an existing sibling via `fetchServerData`:

```ts
{ id: crypto.randomUUID(), user: data.userId, changed: <now-seconds>,
  icon: null, budgetIncome: false, budgetOutcome: false, required: null,
  archive: false, showIncome: false, showOutcome: true, color: null,
  picture: null, title: "2026 Португалия",
  parent: "f05417a6-dc01-44dc-a2e7-abf0fcceae48", staticId: null }
```

Send `{ currentClientTimestamp, serverTimestamp: data.serverTimestamp, tag: [tag] }`. Nesting is **max 1 level** — `parent` must be a top-level tag. Check for an existing same-`title`+`parent` tag first so re-runs don't create twins. Re-run `--prepare` afterwards so the new UUID passes validation.

## Notes

- **Token mode (primary):** single endpoint `POST /v8/diff/`, `Authorization: Bearer <token>`. Transactions are created by POSTing full transaction objects in the diff body — the server requires the **complete** object shape (all nullable props present), built in `toDiffTransactions` (`src/diff-api.ts`). Dates are converted DD.MM.YYYY → `yyyy-MM-dd`. IDs are client-generated **v4 UUIDs** (the server rejects non-UUID ids with a `validationError`).
- **Idempotent submit:** each transaction gets a stable `id` (UUID) assigned at `--prepare` and persisted in `review.json` (`ensureIds`, `src/idempotency.ts`). Because the Diff API **upserts by transaction id**, re-running `--submit-review` updates the same rows instead of creating duplicates. So submitting the same `review.json` twice is safe. (Caveat: the legacy cookie path is still NOT idempotent — the server assigns ids there.)
- **Cookie mode (legacy):** `category: "0"` + `tag_groups: ["id1","id2"]` (array of strings) against `/api/v2/transaction/`; categories from `/api/s1/profile/`.
- The Diff API also returns **existing transactions**, which makes dedup-against-ZenMoney possible (not yet implemented — see plan). `fetchServerData` exposes them.
- All amounts are positive; the `isIncome` flag determines direction.
- The token is long-lived (effectively permanent) — far more stable than `PHPSESSID`. If you get a `401`, re-copy it from budgera.com/settings/api-key.
- Run `bun run test` (= `bun test ./src/*.test.ts`, which covers all four suites and excludes the `zerro/` vendored copy that has its own failing tests) and `bun run typecheck` (`tsc --noEmit`) after code changes — or just `bun run check` to do both.
