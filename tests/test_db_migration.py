import sqlite3

from app.db import init_db
from app.settings import Settings


def test_init_db_migrates_legacy_transactions_table(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          date TEXT NOT NULL,
          direction TEXT NOT NULL CHECK(direction IN ('income','expense')),
          amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
          category TEXT NOT NULL,
          note TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        INSERT INTO transactions(date, direction, amount_cents, category, note)
        VALUES ('2026-03-08', 'expense', 1500, 'food', 'legacy row')
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    columns = [
        row["name"]
        for row in conn2.execute("PRAGMA table_info(transactions)").fetchall()
    ]
    assert "account_id" in columns

    row = conn2.execute("SELECT account_id FROM transactions").fetchone()
    assert row["account_id"] == 1

    account_row = conn2.execute("SELECT id, name FROM accounts WHERE id = 1").fetchone()
    assert account_row["name"] == "Default"
    conn2.close()


def test_init_db_migrates_accounts_table_with_archived_column(tmp_path):
    db_path = tmp_path / "legacy_accounts.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        INSERT INTO accounts(id, name)
        VALUES (1, 'Default'), (2, 'Family')
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    columns = [
        row["name"] for row in conn2.execute("PRAGMA table_info(accounts)").fetchall()
    ]
    assert "archived" in columns

    rows = conn2.execute("SELECT id, archived FROM accounts ORDER BY id ASC").fetchall()
    assert [int(row["archived"]) for row in rows] == [0, 0]
    conn2.close()
