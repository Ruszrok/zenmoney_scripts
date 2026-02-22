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

### 4. Categorize transactions

Map each transaction to a ZenMoney category ID (tag_group ID) using reasoning.

**Category mapping hints** (payee substring → category):
| Payee contains | Category |
|---|---|
| Bolt | Проезд |
| Uber | Проезд |
| Wolt | Кафе и рестораны |
| Lidl, Maxima, Rimi, Barbora | Продукты |
| Circle K, Neste, Viada | Авто / Топливо |
| Telia, Tele2 | Связь |
| Spotify, Netflix, YouTube | Подписки |
| Swedbank, SEB, Luminor | skip — likely a transfer |
| Salary, Palk | Income → Зарплата |

When unsure, set `categoryId: 0` (uncategorized) and note it in the confirmation table.

### 5. Show confirmation table

Present a markdown table to the user for review before submitting:

```
| # | Date | Payee | Amount | In/Out | Category | Comment |
|---|------|-------|--------|--------|----------|---------|
| 1 | 22.02.2026 | Lidl | 45.50 | OUT | Продукты | |
```

Wait for user confirmation before proceeding.

### 6. Dry run

```bash
bun run src/submit.ts --dry-run --cookie "PHPSESSID=xxx" --account "ACCOUNT_ID" <<< '[
  {"date":"22.02.2026","amount":45.50,"payee":"Lidl","comment":"","isIncome":false,"categoryId":650871}
]'
```

### 7. Submit

```bash
bun run src/submit.ts --cookie "PHPSESSID=xxx" --account "ACCOUNT_ID" <<< '[json]'
```

## Other commands

List accounts (to find account ID):
```bash
bun run src/submit.ts --list-accounts --cookie "PHPSESSID=xxx"
```

## Notes

- The PHPSESSID cookie expires; re-fetch from Chrome if you get auth errors
- Account ID for the default account is stored in `config.ts`
- If Claude miscategorizes, add overrides to `CATEGORY_HINTS` in `config.ts`
- All amounts are positive numbers; the `isIncome` flag determines direction
