"""SQLite ledger: schema, connection helpers, settings."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .config import DB_PATH, DEFAULT_SETTINGS, HOUSEHOLD

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, iban TEXT DEFAULT '', currency TEXT DEFAULT '',
  kind TEXT DEFAULT 'bank', created_at TEXT
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('income','expense','transfer','other')),
  description TEXT DEFAULT '', keywords TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, filename TEXT, file_path TEXT,
  account_id INTEGER, status TEXT DEFAULT 'preview', meta TEXT DEFAULT '{}', created_at TEXT
);

-- amount < 0 = money out, amount > 0 = money in
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL,
  description TEXT DEFAULT '', counterparty TEXT DEFAULT '', reference TEXT DEFAULT '',
  category TEXT DEFAULT 'Uncategorized', category_source TEXT DEFAULT '',
  account_id INTEGER, import_id INTEGER, balance_after REAL,
  notes TEXT DEFAULT '', fingerprint TEXT UNIQUE, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_tx_cat ON transactions(category);

CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY, date TEXT, merchant TEXT DEFAULT '', merchant_tax_id TEXT DEFAULT '',
  total REAL, net REAL, vat REAL, currency TEXT, payment_method TEXT DEFAULT '',
  category TEXT DEFAULT 'Uncategorized', items TEXT DEFAULT '[]', vat_lines TEXT DEFAULT '[]',
  file_path TEXT, original_name TEXT, transaction_id INTEGER, status TEXT DEFAULT 'review',
  confidence TEXT DEFAULT '', notes TEXT DEFAULT '', raw TEXT DEFAULT '{}', created_at TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY, direction TEXT NOT NULL CHECK (direction IN ('sales','purchase')),
  number TEXT DEFAULT '', counterparty TEXT DEFAULT '', counterparty_tax_id TEXT DEFAULT '',
  issue_date TEXT, due_date TEXT, net REAL, vat REAL, total REAL, currency TEXT,
  category TEXT DEFAULT 'Uncategorized', status TEXT DEFAULT 'unpaid', transaction_id INTEGER,
  file_path TEXT, original_name TEXT, vat_lines TEXT DEFAULT '[]', notes TEXT DEFAULT '', raw TEXT DEFAULT '{}',
  created_at TEXT
);

-- learned from user corrections: if pattern appears in counterparty/description -> category
CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY, pattern TEXT NOT NULL UNIQUE, category TEXT NOT NULL, created_at TEXT
);

-- email inboxes scanned for receipts (password is write-only through the API)
CREATE TABLE IF NOT EXISTS email_accounts (
  id INTEGER PRIMARY KEY, name TEXT, preset TEXT DEFAULT 'other', host TEXT, port INTEGER DEFAULT 993,
  security TEXT DEFAULT 'ssl', username TEXT, password TEXT, folders TEXT DEFAULT 'INBOX', since_days INTEGER DEFAULT 60,
  enabled INTEGER DEFAULT 1, last_scan_at TEXT, last_status TEXT, created_at TEXT
);

-- every email document seen once: pending -> imported | skipped | error ('ignored' = not a candidate)
CREATE TABLE IF NOT EXISTS email_items (
  id INTEGER PRIMARY KEY, account_id INTEGER, folder TEXT, message_id TEXT, part TEXT, sent_at TEXT, sender TEXT,
  subject TEXT, kind TEXT, file_path TEXT, eml_path TEXT, status TEXT DEFAULT 'pending', doc_type TEXT, doc_id INTEGER,
  reason TEXT DEFAULT '', created_at TEXT, processed_at TEXT, UNIQUE(account_id, message_id, part)
);
CREATE INDEX IF NOT EXISTS ix_email_items_mid ON email_items(message_id);

-- payment cards per account, last 4 digits only (a card, Apple Pay iPhone, Watch... each have their own digits)
CREATE TABLE IF NOT EXISTS account_cards (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, last4 TEXT NOT NULL, label TEXT DEFAULT '', created_at TEXT,
  UNIQUE(last4, account_id)
);

-- cards seen on receipts that are not on any of the user's accounts: never matched, never asked about
CREATE TABLE IF NOT EXISTS ignored_cards (last4 TEXT PRIMARY KEY, note TEXT DEFAULT '', created_at TEXT);

-- receipt uploads waiting for the model (it was offline when they came in): waiting -> done | error
CREATE TABLE IF NOT EXISTS upload_queue (
  id INTEGER PRIMARY KEY, kind TEXT DEFAULT 'receipt', file_path TEXT, original_name TEXT, status TEXT DEFAULT 'waiting',
  reason TEXT DEFAULT '', attempts INTEGER DEFAULT 0, receipt_id INTEGER, document_type TEXT,
  created_at TEXT, last_try_at TEXT, processed_at TEXT
);

-- desktop automation runs (app/desktop.py)
CREATE TABLE IF NOT EXISTS desktop_runs (
  id INTEGER PRIMARY KEY, trigger TEXT, started_at TEXT, finished_at TEXT, status TEXT DEFAULT 'running',
  woke INTEGER DEFAULT 0, shutdown TEXT DEFAULT '', work TEXT DEFAULT '{}', done TEXT DEFAULT '{}', log TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY, filename TEXT, title TEXT, params TEXT, created_at TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dict_factory(cursor, row):
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def connect(readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
    else:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = _dict_factory
    return conn


@contextmanager
def db(readonly: bool = False):
    conn = connect(readonly)
    try:
        yield conn
        if not readonly:
            conn.commit()
    finally:
        conn.close()


# columns added for multi-currency support (also applied to databases created before them)
MIGRATIONS = [
    ("transactions", "orig_amount", "REAL"), ("transactions", "orig_currency", "TEXT DEFAULT ''"),
    ("transactions", "fx_rate", "REAL"), ("transactions", "fx_source", "TEXT DEFAULT ''"), ("transactions", "amount_base", "REAL"),
    ("receipts", "fx_rate", "REAL"), ("receipts", "fx_source", "TEXT DEFAULT ''"), ("receipts", "total_base", "REAL"),
    ("receipts", "net_base", "REAL"), ("receipts", "vat_base", "REAL"),
    ("invoices", "fx_rate", "REAL"), ("invoices", "fx_source", "TEXT DEFAULT ''"), ("invoices", "total_base", "REAL"),
    ("invoices", "net_base", "REAL"), ("invoices", "vat_base", "REAL"),
    ("receipts", "card_last4", "TEXT DEFAULT ''"),
]


def init_db():
    with db() as c:
        c.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            if col not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        from .cards import backfill
        backfill(c)  # card digits the model already read on existing receipts
        from .packs import categories_for, pack_id
        fresh = not c.execute("SELECT 1 FROM settings LIMIT 1").fetchone()
        has_books = bool(c.execute("SELECT 1 FROM transactions LIMIT 1").fetchone()
                         or c.execute("SELECT 1 FROM receipts LIMIT 1").fetchone())
        if not fresh:
            # a database from before these settings existed keeps behaving as it did, instead of taking the new defaults
            legacy = {"country": "Denmark", "base_currency": "DKK", "books_type": "mixed", "vat_registered": "1"}
            for k, v in legacy.items():
                c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        current = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
        pid = pack_id(current)
        flag = "household_categories_added"
        if not c.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
            c.executemany("INSERT INTO categories(name, kind, description, keywords) VALUES (?,?,?,?)", categories_for(pid))
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, '1')", (flag,))
        elif not c.execute("SELECT 1 FROM settings WHERE key = ?", (flag,)).fetchone():
            # an older database: add the private-finance categories once (existing transactions are not touched)
            c.executemany("INSERT OR IGNORE INTO categories(name, kind, description, keywords) VALUES (?,?,?,?)",
                          [x for x in categories_for(pid) if x[0] in HOUSEHOLD])
            c.execute("INSERT INTO settings(key, value) VALUES (?, '1')", (flag,))
        # the first-run setup is only shown for an empty ledger
        if has_books:
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('setup_done', '1')")


def get_settings() -> dict:
    with db() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in rows})
    return s


def set_settings(values: dict):
    with db() as c:
        for k, v in values.items():
            c.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, "" if v is None else str(v)),
            )


def categories() -> list[dict]:
    with db() as c:
        return c.execute("SELECT * FROM categories ORDER BY kind, name").fetchall()


def category_kinds() -> dict:
    return {c["name"]: c["kind"] for c in categories()}


def loads(s, default):
    try:
        return json.loads(s) if s else default
    except (TypeError, ValueError):
        return default
