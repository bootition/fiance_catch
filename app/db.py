import sqlite3
from pathlib import Path

from .settings import Settings


def connect(db_path: str | Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def init_db(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with connect(settings.db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS accounts_updated_at
            AFTER UPDATE ON accounts
            FOR EACH ROW
            BEGIN
              UPDATE accounts SET updated_at = datetime('now') WHERE id = OLD.id;
            END;
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO accounts(id, name)
            VALUES (1, 'Default')
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              account_id INTEGER NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE RESTRICT,
              date TEXT NOT NULL,
              direction TEXT NOT NULL CHECK(direction IN ('income','expense')),
              amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
              category TEXT NOT NULL,
              note TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        if not _column_exists(conn, "transactions", "account_id"):
            conn.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN account_id INTEGER NOT NULL DEFAULT 1
                """
            )
        conn.execute(
            """
            UPDATE transactions
            SET account_id = 1
            WHERE account_id IS NULL
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS transactions_updated_at
            AFTER UPDATE ON transactions
            FOR EACH ROW
            BEGIN
              UPDATE transactions SET updated_at = datetime('now') WHERE id = OLD.id;
            END;
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_transactions_account_date
            ON transactions(account_id, date DESC, id DESC)
            """
        )
