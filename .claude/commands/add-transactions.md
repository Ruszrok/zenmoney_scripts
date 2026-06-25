Parse the attached banking screenshots and add transactions to ZenMoney.

Follow the workflow in CLAUDE.md:

1. Extract transactions from the attached screenshots using vision (date DD.MM.YYYY, amount, payee, comment, isIncome)
2. Get PHPSESSID from Chrome: run `document.cookie` via `mcp__claude-in-chrome__javascript_tool` on a zenmoney.ru tab
3. Categorize each transaction using the category hints in CLAUDE.md and reasoning
4. Run `--prepare` to write `data/review.json` with categories and transactions (include `categoryName` in each transaction)
5. Tell the user to review `data/review.json`
6. After user approves, run `--submit-review` to submit

Use the default bunq account unless the user specifies otherwise: UUID `e30b1cf6-0c08-430d-9a10-c7482d8948f1` in token mode, or integer `11025256` in cookie mode.
