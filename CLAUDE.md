# ZenMoney Transaction Automation

This project automates adding bank transactions to ZenMoney from banking app screenshots.

## Workflow

When the user attaches banking screenshots, follow these steps:

### 1. Parse screenshots

Use vision to extract transactions from each screenshot. For each transaction, extract:
- **date** — format DD.MM.YYYY
- **amount** — numeric, always positive
- **payee** — merchant/sender name
- **comment** — additional details (can be empty)
- **isIncome** — true if money received, false if spent
- Skip **Denied** transactions (they didn't go through)

### 2. Get PHPSESSID from Chrome

Run this via `mcp__claude-in-chrome__javascript_tool` on a zenmoney.ru tab:
```js
document.cookie
```
Extract the `PHPSESSID=...` value from the result.

### 3. Fetch categories

```bash
bun run src/submit.ts --list-categories --cookie "PHPSESSID=xxx"
```

This fetches the full tag_groups hierarchy from `/api/s1/profile/`, including subcategories (e.g. "Проезд / Такси", "Еда / Продукты").

### 4. Categorize transactions

Map each transaction to one or more ZenMoney tag_group IDs using reasoning. Transactions use `categoryIds` (array of numbers) — multiple tag_groups can be applied to a single transaction.

**Category mapping hints** (payee substring → tag_group):
| Payee contains | Category | tag_group ID |
|---|---|---|
| Bolt, Uber | Проезд / Такси | 37480071 |
| Uber Eats, Wolt, Glovo | Еда / Кафе и рестораны | 650876 |
| Lidl, Maxima, Rimi, Barbora, Continente, Pingo Doce, Gleba | Еда / Продукты | 650871 |
| Starbucks, Simit Sarayı, BUGA RAMEN | Еда / Кафе и рестораны | 650876 |
| Decathlon | Спорт | 2357438 |
| Farmacia, Dental, medical | Медицицина | 1143194 |
| Spotify, Netflix, YouTube | Отдых и развлечения / Подписки | 30850494 |
| Swedbank, SEB, Luminor | skip — likely a transfer |  |
| Maksu Services SA | Машина | 650928 |
| Salary, Palk | Зарплата (income) | 650877 |

When unsure, set `categoryIds: []` (uncategorized) and note it in the confirmation table.

### 5. Save review file

Pipe the parsed transactions JSON to `--prepare`, which fetches tag_groups from ZenMoney and writes a review file to `data/review.json`:

```bash
bun run src/submit.ts --prepare --cookie "PHPSESSID=xxx" --account "ACCOUNT_ID" <<< '[
  {"date":"22.02.2026","amount":45.50,"payee":"Lidl","comment":"","isIncome":false,"categoryIds":[650871],"categoryName":"Еда / Продукты"}
]'
```

The review file contains:
- `categories` — all available ZenMoney tag_groups (id, label, type) with full hierarchy
- `transactions` — each transaction with `categoryIds` and `categoryName` for easy review
- `account` — the target account ID

**Always include `categoryName`** in each transaction — it's for user review only and is ignored during submission.

### 6. User reviews `data/review.json`

Wait for the user to review and edit the file. They may change `categoryIds`/`categoryName` or remove transactions.

### 7. Submit from review file

```bash
bun run src/submit.ts --submit-review --cookie "PHPSESSID=xxx"
```

This reads `data/review.json` and submits all transactions in it.

## Other commands

List accounts (to find account ID):
```bash
bun run src/submit.ts --list-accounts --cookie "PHPSESSID=xxx"
```

## Notes

- The PHPSESSID cookie expires; re-fetch from Chrome if you get auth errors
- Account ID for the default account is stored in `config.ts`
- All amounts are positive numbers; the `isIncome` flag determines direction
- ZenMoney API uses `category: "0"` + `tag_groups: ["id1", "id2"]` for categorization
- The old `tag_group` (singular) field is deprecated; always use `tag_groups` (array of strings)
- Categories are fetched from `/api/s1/profile/` which returns the full tag_groups + tags hierarchy
