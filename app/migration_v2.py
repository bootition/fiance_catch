import sqlite3
from datetime import datetime
from pathlib import Path

from .settings import Settings

LEDGER_V2_MARKER = "ledger_v2_init_at"


def _connect(db_path: str | Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

_NEW_TABLES = (
    "schema_meta",
    "import_batches",
    "source_transactions",
    "ledger_entries",
    "review_queue",
    "refund_links",
    "classification_rules",
    "entry_audit_events",
)


def new_schema_initialized(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM sqlite_master
        WHERE type = 'table' AND name IN (%s)
        """ % ",".join("?" * len(_NEW_TABLES)),
        _NEW_TABLES,
    ).fetchone()
    return int(row["c"]) == len(_NEW_TABLES)


def backup_db(settings: Settings) -> Path:
    """用 SQLite backup API 创建一致性快照（含 WAL 未合并事务）并校验完整性。"""
    if not settings.db_path.exists():
        raise FileNotFoundError(f"database not found: {settings.db_path}")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = settings.data_dir / f"ledger.sqlite-{timestamp}.bak"
    with sqlite3.connect(str(settings.db_path)) as src, sqlite3.connect(
        str(backup_path)
    ) as dst:
        src.backup(dst)
    with _connect(backup_path) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result is None or str(result[0]).lower() != "ok":
            raise RuntimeError(f"backup integrity check failed: {backup_path}")
    return backup_path


def init_new_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_batches (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          file_name TEXT NOT NULL,
          platform TEXT NOT NULL CHECK(platform IN ('alipay','wechat')),
          file_fingerprint TEXT NOT NULL,
          imported_at TEXT NOT NULL DEFAULT (datetime('now')),
          status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
          row_count INTEGER NOT NULL DEFAULT 0 CHECK(row_count >= 0),
          accepted_count INTEGER NOT NULL DEFAULT 0 CHECK(accepted_count >= 0),
          skipped_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_count >= 0),
          pending_count INTEGER NOT NULL DEFAULT 0 CHECK(pending_count >= 0),
          revoked_at TEXT,
          revoke_note TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          platform TEXT NOT NULL CHECK(platform IN ('alipay','wechat')),
          source_txn_id TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
          direction TEXT NOT NULL CHECK(direction IN ('expense','income','neutral')),
          status_text TEXT NOT NULL,
          counterparty TEXT NOT NULL DEFAULT '',
          item_desc TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          batch_id INTEGER REFERENCES import_batches(id) ON DELETE RESTRICT,
          normalized_hash TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(platform, source_txn_id)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ledger_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entry_type TEXT NOT NULL CHECK(entry_type IN ('consumption','income','transfer','refund')),
          amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
          category TEXT NOT NULL DEFAULT '',
          txn_date TEXT NOT NULL,
          source_transaction_id INTEGER REFERENCES source_transactions(id) ON DELETE RESTRICT,
          batch_id INTEGER REFERENCES import_batches(id) ON DELETE RESTRICT,
          manual_edited INTEGER NOT NULL DEFAULT 0 CHECK(manual_edited IN (0,1)),
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_queue (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_transaction_id INTEGER NOT NULL REFERENCES source_transactions(id) ON DELETE CASCADE,
          reason TEXT NOT NULL,
          priority INTEGER NOT NULL DEFAULT 1 CHECK(priority BETWEEN 0 AND 5),
          suggested_category TEXT NOT NULL DEFAULT '',
          suggested_type TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','resolved','dismissed')),
          resolved_ledger_id INTEGER REFERENCES ledger_entries(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          resolved_at TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS refund_links (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          refund_source_id INTEGER NOT NULL REFERENCES source_transactions(id) ON DELETE RESTRICT,
          original_ledger_id INTEGER NOT NULL REFERENCES ledger_entries(id) ON DELETE RESTRICT,
          refund_amount_cents INTEGER NOT NULL CHECK(refund_amount_cents >= 0),
          linked_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(refund_source_id, original_ledger_id)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS classification_rules (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_field TEXT NOT NULL CHECK(match_field IN ('counterparty','item_desc')),
          match_pattern TEXT NOT NULL,
          target_type TEXT NOT NULL CHECK(target_type IN ('consumption','income','transfer')),
          target_category TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'observing' CHECK(status IN ('observing','active','disabled')),
          hit_count INTEGER NOT NULL DEFAULT 0 CHECK(hit_count >= 0),
          confirm_count INTEGER NOT NULL DEFAULT 0 CHECK(confirm_count >= 0),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entry_audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_type TEXT NOT NULL CHECK(event_type IN ('manual_edit','bulk_confirm','rule_applied','refund_linked','batch_revoked')),
          ref_ledger_id INTEGER,
          ref_rule_id INTEGER,
          ref_batch_id INTEGER,
          detail TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_transactions_occurred_at "
        "ON source_transactions(occurred_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_transactions_batch_id "
        "ON source_transactions(batch_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_entries_txn_date "
        "ON ledger_entries(txn_date DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_entries_source_txn "
        "ON ledger_entries(source_transaction_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_entries_batch_id "
        "ON ledger_entries(batch_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_queue_status_priority "
        "ON review_queue(status, priority DESC, id ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_refund_links_original "
        "ON refund_links(original_ledger_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classification_rules_status "
        "ON classification_rules(status, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_created "
        "ON entry_audit_events(created_at DESC, id DESC)"
    )


_LEGACY_TABLES = (
    "transactions",
    "import_rows",
    "import_sessions",
    "category_rules",
    "accounts",
)


def drop_legacy_tables(conn: sqlite3.Connection) -> None:
    """删除旧业务表（索引/触发器随表删除）。幂等，表不存在时静默跳过。"""
    for table in _LEGACY_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")


_LEGACY_DATA_TABLES = (
    "transactions",
    "import_sessions",
    "import_rows",
    "category_rules",
)


def _legacy_has_data(conn: sqlite3.Connection) -> bool:
    """旧 schema 任意表存在数据行才需要备份（全新空库不备份）。"""
    for table in _LEGACY_DATA_TABLES:
        exists = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table,),
        ).fetchone()
        if exists is None:
            continue
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
        if int(row["c"]) > 0:
            return True
    return False


def ensure_ledger_v2(settings: Settings) -> bool:
    """幂等重置迁移：保证活动库为干净的新模型。

    - 旧库有数据（无论新 schema 是否已初始化，兼容历史半迁移状态）：
      先做一致性备份，再在单事务中删除旧业务表、确保新 schema 与 marker。
    - 无旧数据（全新库）：不备份，直接确保新 schema。
    返回是否执行了备份迁移。旧库数据只存在于时间戳备份文件。
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with _connect(settings.db_path) as conn:
        initialized = new_schema_initialized(conn)
        has_legacy = _legacy_has_data(conn)
    backed_up = False
    if has_legacy:
        backup_db(settings)
        backed_up = True
    with _connect(settings.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        drop_legacy_tables(conn)
        if not initialized:
            init_new_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO schema_meta(key, value)
                VALUES (?, ?)
                """,
                (LEDGER_V2_MARKER, datetime.now().isoformat(timespec="seconds")),
            )
        conn.commit()
    return backed_up
