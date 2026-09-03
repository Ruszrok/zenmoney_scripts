CREATE TABLE IF NOT EXISTS accounts (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  currency   TEXT,
  kind       TEXT,
  alias_of   INTEGER REFERENCES accounts(id),
  opening_balance_minor INTEGER,
  opening_date TEXT
);

CREATE TABLE IF NOT EXISTS categories (
  id        INTEGER PRIMARY KEY,
  full_name TEXT NOT NULL UNIQUE,
  parent    TEXT,
  leaf      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
  id                 TEXT PRIMARY KEY,
  date               TEXT NOT NULL,
  category_id        INTEGER REFERENCES categories(id),
  payee              TEXT NOT NULL DEFAULT '',
  comment            TEXT NOT NULL DEFAULT '',
  outcome_account_id INTEGER REFERENCES accounts(id),
  outcome_minor      INTEGER NOT NULL DEFAULT 0,
  outcome_currency   TEXT NOT NULL DEFAULT '',
  income_account_id  INTEGER REFERENCES accounts(id),
  income_minor       INTEGER NOT NULL DEFAULT 0,
  income_currency    TEXT NOT NULL DEFAULT '',
  kind               TEXT NOT NULL CHECK (kind IN ('outcome','income','transfer')),
  outcome_eur_minor  INTEGER,
  income_eur_minor   INTEGER,
  fx_source          TEXT,
  created_at         TEXT,
  changed_at         TEXT,
  source_file        TEXT,
  imported_at        TEXT,
  deleted_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_date    ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_kind    ON transactions(kind);
CREATE INDEX IF NOT EXISTS idx_tx_cat     ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_tx_deleted ON transactions(deleted_at);

CREATE TABLE IF NOT EXISTS fx_rates (
  date         TEXT NOT NULL,
  currency     TEXT NOT NULL,
  eur_per_unit REAL NOT NULL,
  source       TEXT NOT NULL CHECK (source IN ('ecb','implied','manual','filled')),
  PRIMARY KEY (date, currency)
);

CREATE TABLE IF NOT EXISTS import_batches (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at        TEXT NOT NULL,
  files         TEXT NOT NULL,
  rows_seen     INTEGER NOT NULL DEFAULT 0,
  rows_new      INTEGER NOT NULL DEFAULT 0,
  rows_updated  INTEGER NOT NULL DEFAULT 0,
  rows_deleted  INTEGER NOT NULL DEFAULT 0
);

DROP VIEW IF EXISTS v_transactions;
CREATE VIEW v_transactions AS
SELECT
  t.id, t.date, substr(t.date, 1, 7) AS month, t.kind,
  c.full_name AS category, c.parent AS category_parent, c.leaf AS category_leaf,
  t.payee, t.comment,
  oa.name AS outcome_account, oa.kind AS outcome_account_kind,
  t.outcome_minor / 100.0 AS outcome, t.outcome_currency,
  t.outcome_eur_minor / 100.0 AS outcome_eur,
  ia.name AS income_account, ia.kind AS income_account_kind,
  t.income_minor / 100.0 AS income, t.income_currency,
  t.income_eur_minor / 100.0 AS income_eur,
  t.fx_source
FROM transactions t
LEFT JOIN categories c  ON c.id  = t.category_id
LEFT JOIN accounts   oa ON oa.id = t.outcome_account_id
LEFT JOIN accounts   ia ON ia.id = t.income_account_id
WHERE t.deleted_at IS NULL;

DROP VIEW IF EXISTS v_spend;
CREATE VIEW v_spend AS
SELECT * FROM v_transactions
WHERE kind = 'outcome'
  AND (category IS NULL OR category <> 'Корректировка')
  AND (outcome_account_kind IS NULL
       OR outcome_account_kind NOT IN ('savings', 'investment'));

DROP VIEW IF EXISTS v_income;
CREATE VIEW v_income AS
SELECT *, CASE WHEN category = 'проценты' THEN 1 ELSE 0 END AS is_passive
FROM v_transactions
WHERE kind = 'income'
  AND (category IS NULL OR category <> 'Корректировка');

DROP VIEW IF EXISTS v_monthly;
CREATE VIEW v_monthly AS
SELECT month,
       COALESCE(category, '(uncategorised)') AS category,
       category_parent,
       SUM(COALESCE(outcome_eur, 0)) AS spend_eur,
       COUNT(*) AS transactions
FROM v_spend
GROUP BY month, category, category_parent;
