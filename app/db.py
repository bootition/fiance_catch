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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_sql(conn: sqlite3.Connection, table_name: str) -> str:
    row = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    if row is None or row["sql"] is None:
        return ""
    return str(row["sql"])


def _accounts_has_archived_check(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "accounts"):
        return False
    normalized = "".join(_table_sql(conn, "accounts").lower().split())
    return "check(archivedin(0,1))" in normalized


def _transactions_has_account_fk(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "transactions"):
        return False
    rows = conn.execute("PRAGMA foreign_key_list(transactions)").fetchall()
    for row in rows:
        if (
            row["from"] == "account_id"
            and row["table"] == "accounts"
            and row["to"] == "id"
            and str(row["on_delete"]).upper() == "RESTRICT"
        ):
            return True
    return False


def _transactions_has_neutral_direction_check(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "transactions"):
        return False
    normalized = "".join(_table_sql(conn, "transactions").lower().split())
    if "check(directionin(" not in normalized:
        return False
    return (
        "'income'" in normalized
        and "'expense'" in normalized
        and "'neutral'" in normalized
    )


def _create_accounts_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def _create_transactions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE RESTRICT,
          date TEXT NOT NULL,
          direction TEXT NOT NULL CHECK(direction IN ('income','expense','neutral')),
          amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
          category TEXT NOT NULL,
          note TEXT NOT NULL,
          source_txn_id TEXT,
          import_batch_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def _create_import_sessions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_sessions (
          id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          source_name TEXT NOT NULL,
          lang TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('active','committed','discarded')),
          include_neutral INTEGER NOT NULL DEFAULT 1 CHECK(include_neutral IN (0,1))
        );
        """
    )


def _create_import_rows_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_rows (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL REFERENCES import_sessions(id) ON DELETE CASCADE,
          row_no INTEGER NOT NULL,
          date TEXT,
          direction TEXT,
          amount_cents INTEGER,
          category TEXT,
          raw_category TEXT,
          note TEXT,
          status_text TEXT NOT NULL DEFAULT '',
          parse_status TEXT NOT NULL CHECK(parse_status IN ('valid','skipped_status','invalid')),
          parse_error TEXT NOT NULL DEFAULT '',
          source_txn_id TEXT,
          tag TEXT NOT NULL DEFAULT '',
          selected INTEGER NOT NULL DEFAULT 0 CHECK(selected IN (0,1)),
          deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1))
        );
        """
    )


def _create_category_rules_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS category_rules (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_pattern TEXT NOT NULL,
          target_category TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(match_pattern, target_category)
        );
        """
    )


def _rebuild_accounts_table(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE accounts RENAME TO accounts__legacy")
    _create_accounts_table(conn)
    conn.execute(
        """
        INSERT INTO accounts(id, name, archived, created_at, updated_at)
        SELECT
          id,
          name,
          CASE WHEN archived IN (0, 1) THEN archived ELSE 0 END,
          COALESCE(created_at, datetime('now')),
          COALESCE(updated_at, datetime('now'))
        FROM accounts__legacy
        ORDER BY id ASC
        """
    )
    conn.execute("DROP TABLE accounts__legacy")


def _rebuild_transactions_table(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE transactions RENAME TO transactions__legacy")
    _create_transactions_table(conn)

    if not _column_exists(conn, "transactions__legacy", "source_txn_id"):
        conn.execute(
            """
            ALTER TABLE transactions__legacy
            ADD COLUMN source_txn_id TEXT
            """
        )

    if not _column_exists(conn, "transactions__legacy", "import_batch_id"):
        conn.execute(
            """
            ALTER TABLE transactions__legacy
            ADD COLUMN import_batch_id TEXT
            """
        )

    if _column_exists(conn, "transactions__legacy", "account_id"):
        conn.execute(
            """
            INSERT INTO transactions(
              id,
              account_id,
              date,
              direction,
              amount_cents,
              category,
              note,
              source_txn_id,
              import_batch_id,
              created_at,
              updated_at
            )
            SELECT
              t.id,
              CASE
                WHEN t.account_id IS NULL THEN 1
                WHEN EXISTS(SELECT 1 FROM accounts WHERE id = t.account_id) THEN t.account_id
                ELSE 1
              END,
              t.date,
              t.direction,
              t.amount_cents,
              t.category,
              t.note,
              t.source_txn_id,
              t.import_batch_id,
              COALESCE(t.created_at, datetime('now')),
              COALESCE(t.updated_at, datetime('now'))
            FROM transactions__legacy AS t
            ORDER BY t.id ASC
            """
        )
    else:
        conn.execute(
            """
            INSERT INTO transactions(
              id,
              account_id,
              date,
              direction,
              amount_cents,
              category,
              note,
              source_txn_id,
              import_batch_id,
              created_at,
              updated_at
            )
            SELECT
              id,
              1,
              date,
              direction,
              amount_cents,
              category,
              note,
              source_txn_id,
              import_batch_id,
              COALESCE(created_at, datetime('now')),
              COALESCE(updated_at, datetime('now'))
            FROM transactions__legacy
            ORDER BY id ASC
            """
        )

    conn.execute("DROP TABLE transactions__legacy")


def init_db(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with connect(settings.db_path) as conn:
        _create_accounts_table(conn)
        if not _column_exists(conn, "accounts", "archived"):
            conn.execute(
                """
                ALTER TABLE accounts
                ADD COLUMN archived INTEGER NOT NULL DEFAULT 0
                """
            )
        conn.execute(
            """
            UPDATE accounts
            SET archived = 0
            WHERE archived IS NULL
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO accounts(id, name)
            VALUES (1, 'Default')
            """
        )
        _create_transactions_table(conn)
        _create_import_sessions_table(conn)
        _create_import_rows_table(conn)
        _create_category_rules_table(conn)
        if not _column_exists(conn, "transactions", "account_id"):
            conn.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN account_id INTEGER NOT NULL DEFAULT 1
                """
            )
        if not _column_exists(conn, "transactions", "source_txn_id"):
            conn.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN source_txn_id TEXT
                """
            )
        if not _column_exists(conn, "transactions", "import_batch_id"):
            conn.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN import_batch_id TEXT
                """
            )
        conn.execute(
            """
            UPDATE transactions
            SET account_id = 1
            WHERE account_id IS NULL
            """
        )

        needs_accounts_rebuild = not _accounts_has_archived_check(conn)
        needs_transactions_rebuild = not _transactions_has_account_fk(conn)
        if not _transactions_has_neutral_direction_check(conn):
            needs_transactions_rebuild = True
        if needs_accounts_rebuild:
            needs_transactions_rebuild = True

        if needs_accounts_rebuild or needs_transactions_rebuild:
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF;")

            if needs_accounts_rebuild:
                _rebuild_accounts_table(conn)

            conn.execute(
                """
                INSERT OR IGNORE INTO accounts(id, name)
                VALUES (1, 'Default')
                """
            )

            if needs_transactions_rebuild:
                _rebuild_transactions_table(conn)

            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON;")

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
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_transactions_import_batch_id
            ON transactions(import_batch_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_import_rows_session_row_no
            ON import_rows(session_id, row_no ASC, id ASC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_import_rows_session_selected
            ON import_rows(session_id, selected, deleted)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_category_rules_enabled
            ON category_rules(enabled, id)
            """
        )
        conn.execute("DROP INDEX IF EXISTS idx_transactions_source_txn_id_unique")
