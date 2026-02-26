import sqlite3
from pathlib import Path

from .settings import Settings


def connect(db_path: str | Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with connect(settings.db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
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
