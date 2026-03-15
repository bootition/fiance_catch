import sqlite3

from app.db import init_db
from app.repo import get_summary, list_txns
from app.settings import Settings


def test_init_db_rebuilds_legacy_transactions_table_with_account_fk(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("INSERT INTO accounts(id, name, archived) VALUES (1, 'Default', 0)")
    conn.execute("INSERT INTO accounts(id, name, archived) VALUES (2, 'Family', 0)")
    conn.execute(
        """
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL DEFAULT 1,
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
        INSERT INTO transactions(account_id, date, direction, amount_cents, category, note)
        VALUES (2, '2026-03-08', 'expense', 1500, 'food', 'legacy row')
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    fk_rows = conn2.execute("PRAGMA foreign_key_list(transactions)").fetchall()
    assert fk_rows
    assert any(
        row["table"] == "accounts"
        and row["from"] == "account_id"
        and row["to"] == "id"
        and str(row["on_delete"]).upper() == "RESTRICT"
        for row in fk_rows
    )

    column_rows = conn2.execute("PRAGMA table_info(transactions)").fetchall()
    account_id_column = [row for row in column_rows if row["name"] == "account_id"][0]
    assert int(account_id_column["notnull"]) == 1
    assert str(account_id_column["dflt_value"]).strip("'\"") == "1"

    row = conn2.execute("SELECT account_id FROM transactions").fetchone()
    assert row["account_id"] == 2

    txn_trigger = conn2.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'trigger' AND name = 'transactions_updated_at'
        """
    ).fetchone()
    assert txn_trigger is not None

    txn_index = conn2.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_transactions_account_date'
        """
    ).fetchone()
    assert txn_index is not None
    conn2.close()


def test_init_db_rebuilds_legacy_accounts_table_with_archived_check(tmp_path):
    db_path = tmp_path / "legacy_accounts.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        INSERT INTO accounts(id, name, archived)
        VALUES (1, 'Default', 0), (2, 'Family', 1)
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    table_sql = conn2.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'accounts'
        """
    ).fetchone()["sql"]
    normalized_sql = "".join(str(table_sql).lower().split())
    assert "check(archivedin(0,1))" in normalized_sql

    rows = conn2.execute("SELECT id, archived FROM accounts ORDER BY id ASC").fetchall()
    assert [int(row["archived"]) for row in rows] == [0, 1]

    account_trigger = conn2.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'trigger' AND name = 'accounts_updated_at'
        """
    ).fetchone()
    assert account_trigger is not None
    conn2.close()


def test_init_db_normalizes_legacy_transaction_account_id_edge_cases(tmp_path):
    db_path = tmp_path / "legacy_dirty_txn.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("INSERT INTO accounts(id, name, archived) VALUES (1, 'Default', 0)")
    conn.execute("INSERT INTO accounts(id, name, archived) VALUES (2, 'Family', 0)")
    conn.execute(
        """
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER,
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
        INSERT INTO transactions(account_id, date, direction, amount_cents, category, note)
        VALUES
          (NULL, '2026-03-08', 'expense', 100, 'misc', 'null-account'),
          (999, '2026-03-09', 'expense', 200, 'misc', 'missing-account'),
          (2, '2026-03-10', 'income', 300, 'salary', 'valid-account')
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    rows = conn2.execute(
        "SELECT id, account_id, note FROM transactions ORDER BY id ASC"
    ).fetchall()
    assert [int(row["account_id"]) for row in rows] == [1, 1, 2]

    fk_rows = conn2.execute("PRAGMA foreign_key_list(transactions)").fetchall()
    assert any(
        row["table"] == "accounts"
        and row["from"] == "account_id"
        and row["to"] == "id"
        and str(row["on_delete"]).upper() == "RESTRICT"
        for row in fk_rows
    )

    txn_trigger = conn2.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'trigger' AND name = 'transactions_updated_at'
        """
    ).fetchone()
    assert txn_trigger is not None

    txn_index = conn2.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_transactions_account_date'
        """
    ).fetchone()
    assert txn_index is not None
    conn2.close()


def test_init_db_normalizes_legacy_accounts_archived_edge_cases(tmp_path):
    db_path = tmp_path / "legacy_dirty_accounts.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          archived INTEGER DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        INSERT INTO accounts(id, name, archived)
        VALUES
          (1, 'Default', 0),
          (2, 'Family', 7),
          (3, 'Travel', -1),
          (4, 'Other', NULL)
        """
    )
    conn.execute(
        """
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER,
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
        INSERT INTO transactions(account_id, date, direction, amount_cents, category, note)
        VALUES (2, '2026-03-08', 'expense', 500, 'food', 'kept')
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    archived_rows = conn2.execute(
        "SELECT id, archived FROM accounts ORDER BY id ASC"
    ).fetchall()
    assert [int(row["archived"]) for row in archived_rows] == [0, 0, 0, 0]

    table_sql = conn2.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'accounts'
        """
    ).fetchone()["sql"]
    normalized_sql = "".join(str(table_sql).lower().split())
    assert "check(archivedin(0,1))" in normalized_sql

    account_trigger = conn2.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'trigger' AND name = 'accounts_updated_at'
        """
    ).fetchone()
    assert account_trigger is not None

    fk_rows = conn2.execute("PRAGMA foreign_key_list(transactions)").fetchall()
    assert any(
        row["table"] == "accounts"
        and row["from"] == "account_id"
        and row["to"] == "id"
        and str(row["on_delete"]).upper() == "RESTRICT"
        for row in fk_rows
    )

    txn_trigger = conn2.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'trigger' AND name = 'transactions_updated_at'
        """
    ).fetchone()
    assert txn_trigger is not None

    txn_index = conn2.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_transactions_account_date'
        """
    ).fetchone()
    assert txn_index is not None
    conn2.close()


def test_single_ledger_reads_all_legacy_account_rows(tmp_path):
    db_path = tmp_path / "legacy_multi_account_view.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("INSERT INTO accounts(id, name, archived) VALUES (1, 'Default', 0)")
    conn.execute("INSERT INTO accounts(id, name, archived) VALUES (2, 'Family', 0)")
    conn.execute(
        """
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE RESTRICT,
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
        INSERT INTO transactions(account_id, date, direction, amount_cents, category, note)
        VALUES
          (1, '2026-03-08', 'expense', 100, 'misc', 'default-row'),
          (2, '2026-03-09', 'expense', 200, 'misc', 'family-row')
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    notes = [str(row["note"]) for row in rows]
    assert "default-row" in notes
    assert "family-row" in notes

    summary = get_summary(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert summary["expense_cents"] == 300


def test_init_db_drops_legacy_source_txn_unique_index(tmp_path):
    db_path = tmp_path / "legacy_source_txn_index.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("INSERT INTO accounts(id, name, archived) VALUES (1, 'Default', 0)")
    conn.execute(
        """
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE RESTRICT,
          date TEXT NOT NULL,
          direction TEXT NOT NULL CHECK(direction IN ('income','expense')),
          amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
          category TEXT NOT NULL,
          note TEXT NOT NULL,
          source_txn_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_transactions_source_txn_id_unique
        ON transactions(source_txn_id)
        WHERE source_txn_id IS NOT NULL
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    index_row = conn2.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_transactions_source_txn_id_unique'
        """
    ).fetchone()
    assert index_row is None
    conn2.close()


def test_init_db_adds_import_batch_id_column_and_index(tmp_path):
    db_path = tmp_path / "legacy_import_batch.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("INSERT INTO accounts(id, name, archived) VALUES (1, 'Default', 0)")
    conn.execute(
        """
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE RESTRICT,
          date TEXT NOT NULL,
          direction TEXT NOT NULL CHECK(direction IN ('income','expense')),
          amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
          category TEXT NOT NULL,
          note TEXT NOT NULL,
          source_txn_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    columns = conn2.execute("PRAGMA table_info(transactions)").fetchall()
    assert any(row["name"] == "import_batch_id" for row in columns)

    idx_row = conn2.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_transactions_import_batch_id'
        """
    ).fetchone()
    assert idx_row is not None
    conn2.close()


def test_init_db_upgrades_direction_check_to_include_neutral(tmp_path):
    db_path = tmp_path / "legacy_direction_check.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("INSERT INTO accounts(id, name, archived) VALUES (1, 'Default', 0)")
    conn.execute(
        """
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE RESTRICT,
          date TEXT NOT NULL,
          direction TEXT NOT NULL CHECK(direction IN ('income','expense')),
          amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
          category TEXT NOT NULL,
          note TEXT NOT NULL,
          source_txn_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        INSERT INTO transactions(account_id, date, direction, amount_cents, category, note, source_txn_id)
        VALUES (1, '2026-03-10', 'expense', 100, 'food', 'legacy-expense', 'LEGACY001')
        """
    )
    conn.commit()
    conn.close()

    settings = Settings(data_dir=tmp_path, db_path=db_path)
    init_db(settings)

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    table_sql = conn2.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'transactions'
        """
    ).fetchone()["sql"]
    normalized_sql = "".join(str(table_sql).lower().split())
    assert "check(directionin('income','expense','neutral'))" in normalized_sql

    rows = conn2.execute(
        "SELECT direction, note FROM transactions WHERE note = 'legacy-expense'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["direction"] == "expense"
    conn2.close()
