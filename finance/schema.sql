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
