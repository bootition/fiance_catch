import sqlite3
from datetime import datetime
from pathlib import Path

from .settings import Settings

LEDGER_V2_MARKER = "ledger_v2_init_at"
SCHEMA_VERSION_KEY = "schema_version"
SCHEMA_VERSION = 6  # 5 = 审计事件类型扩展；6 = 规则平台/方向条件 + bulk_reopen 审计


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
          raw_type TEXT NOT NULL DEFAULT '',
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
          refund_source_id INTEGER NOT NULL UNIQUE REFERENCES source_transactions(id) ON DELETE RESTRICT,
          original_ledger_id INTEGER NOT NULL REFERENCES ledger_entries(id) ON DELETE RESTRICT,
          refund_amount_cents INTEGER NOT NULL CHECK(refund_amount_cents >= 0),
          linked_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS classification_rules (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_field TEXT NOT NULL CHECK(match_field IN ('counterparty','item_desc')),
          match_pattern TEXT NOT NULL CHECK(TRIM(match_pattern) <> ''),
          platform TEXT NOT NULL DEFAULT '' CHECK(platform IN ('','alipay','wechat')),
          direction TEXT NOT NULL DEFAULT '' CHECK(direction IN ('','expense','income','neutral')),
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
          event_type TEXT NOT NULL CHECK(event_type IN ('manual_edit','bulk_confirm','rule_applied','refund_linked','batch_revoked','high_risk_resolved','bulk_reopen')),
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


def _ensure_v2_columns(conn: sqlite3.Connection) -> None:
    """为已初始化的 v2 库补齐后续新增列（幂等）。"""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(source_transactions)").fetchall()
    }
    if "raw_type" not in cols:
        conn.execute(
            "ALTER TABLE source_transactions ADD COLUMN raw_type TEXT NOT NULL DEFAULT ''"
        )

    rule_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(classification_rules)").fetchall()
    }
    if "platform" not in rule_cols:
        conn.execute(
            "ALTER TABLE classification_rules ADD COLUMN platform TEXT NOT NULL DEFAULT ''"
        )
    if "direction" not in rule_cols:
        conn.execute(
            "ALTER TABLE classification_rules ADD COLUMN direction TEXT NOT NULL DEFAULT ''"
        )


def _classification_rules_has_blank_check(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'classification_rules'
        """
    ).fetchone()
    if row is None or row["sql"] is None:
        return False
    normalized = "".join(str(row["sql"]).lower().split())
    return "trim(match_pattern)<>''" in normalized


def _rebuild_classification_rules(conn: sqlite3.Connection) -> int:
    """重建 classification_rules 以加入空模式 CHECK。

    阶段 2 允许空 match_pattern 的规则无业务意义且会使新 CHECK 失败，
    迁移时过滤不复制；返回被隔离的空白模式规则数量（可追溯）。
    """
    conn.execute("ALTER TABLE classification_rules RENAME TO classification_rules__legacy")
    dropped = int(
        conn.execute(
            """
            SELECT COUNT(*) AS c FROM classification_rules__legacy
            WHERE TRIM(match_pattern) = ''
            """
        ).fetchone()["c"]
    )
    conn.execute(
        """
        CREATE TABLE classification_rules (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_field TEXT NOT NULL CHECK(match_field IN ('counterparty','item_desc')),
          match_pattern TEXT NOT NULL CHECK(TRIM(match_pattern) <> ''),
          platform TEXT NOT NULL DEFAULT '' CHECK(platform IN ('','alipay','wechat')),
          direction TEXT NOT NULL DEFAULT '' CHECK(direction IN ('','expense','income','neutral')),
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
        INSERT INTO classification_rules(
          id, match_field, match_pattern, platform, direction, target_type,
          target_category, status, hit_count, confirm_count, created_at, updated_at
        )
        SELECT
          id, match_field, match_pattern,
          COALESCE(platform, ''), COALESCE(direction, ''),
          target_type, target_category,
          status, hit_count, confirm_count, created_at, updated_at
        FROM classification_rules__legacy
        WHERE TRIM(match_pattern) <> ''
        ORDER BY id ASC
        """
    )
    conn.execute("DROP TABLE classification_rules__legacy")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classification_rules_status "
        "ON classification_rules(status, id)"
    )
    return dropped


def _repair_masked_counterparty_rules(conn: sqlite3.Connection) -> int:
    """把脱敏商户自动规则改写为商品说明规则（v6 迁移）。"""
    masked = conn.execute(
        """
        SELECT * FROM classification_rules
        WHERE match_field = 'counterparty'
          AND (match_pattern LIKE '%*%' OR match_pattern IN ('', '/', '-'))
        ORDER BY id ASC
        """
    ).fetchall()
    repaired = 0
    for rule in masked:
        representative = conn.execute(
            """
            SELECT st.item_desc AS item_desc, st.platform AS platform, st.direction AS direction
            FROM entry_audit_events AS e
            JOIN ledger_entries AS le ON le.id = e.ref_ledger_id
            JOIN source_transactions AS st ON st.id = le.source_transaction_id
            WHERE e.ref_rule_id = ?
              AND e.event_type = 'bulk_confirm'
              AND TRIM(st.item_desc) <> ''
              AND st.item_desc NOT IN ('/', '-')
            GROUP BY st.item_desc, st.platform, st.direction
            ORDER BY COUNT(*) DESC, st.item_desc ASC
            LIMIT 1
            """,
            (int(rule["id"]),),
        ).fetchone()
        if representative is None:
            conn.execute("DELETE FROM classification_rules WHERE id = ?", (int(rule["id"]),))
            continue
        pattern = str(representative["item_desc"]).strip()
        if not pattern:
            conn.execute("DELETE FROM classification_rules WHERE id = ?", (int(rule["id"]),))
            continue
        conn.execute(
            """
            UPDATE classification_rules
            SET match_field = 'item_desc',
                match_pattern = ?,
                platform = ?,
                direction = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (pattern, representative["platform"], representative["direction"], int(rule["id"])),
        )
        repaired += 1
    return repaired


def _current_schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return None


def _refund_links_has_source_unique(conn: sqlite3.Connection) -> bool:
    """检测 refund_links 是否有 refund_source_id 唯一约束（列级/表级均可）。"""
    for index in conn.execute("PRAGMA index_list(refund_links)").fetchall():
        if not int(index["unique"]):
            continue
        cols = conn.execute(
            f"PRAGMA index_info({index['name']})"
        ).fetchall()
        if any(col["name"] == "refund_source_id" for col in cols):
            return True
    return False


def _rebuild_refund_links(conn: sqlite3.Connection) -> int:
    """重建 refund_links 使 refund_source_id 唯一；返回清理的多重链接数。

    旧库（v3）允许同一退款来源关联多笔消费，属脏数据：
    每源保留最早一条（id 最小），其余隔离（仅计数记录，不静默丢失）。
    """
    conn.execute("ALTER TABLE refund_links RENAME TO refund_links__legacy")
    duplicates = int(
        conn.execute(
            """
            SELECT COUNT(*) AS c FROM refund_links__legacy AS r
            WHERE EXISTS (
              SELECT 1 FROM refund_links__legacy AS other
              WHERE other.refund_source_id = r.refund_source_id
                AND other.id < r.id
            )
            """
        ).fetchone()["c"]
    )
    conn.execute(
        """
        CREATE TABLE refund_links (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          refund_source_id INTEGER NOT NULL UNIQUE REFERENCES source_transactions(id) ON DELETE RESTRICT,
          original_ledger_id INTEGER NOT NULL REFERENCES ledger_entries(id) ON DELETE RESTRICT,
          refund_amount_cents INTEGER NOT NULL CHECK(refund_amount_cents >= 0),
          linked_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        INSERT INTO refund_links(
          id, refund_source_id, original_ledger_id, refund_amount_cents, linked_at
        )
        SELECT
          MIN(id), refund_source_id, original_ledger_id, refund_amount_cents, linked_at
        FROM refund_links__legacy
        GROUP BY refund_source_id
        ORDER BY MIN(id) ASC
        """
    )
    conn.execute("DROP TABLE refund_links__legacy")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_refund_links_original "
        "ON refund_links(original_ledger_id)"
    )
    return duplicates


def _audit_events_has_high_risk_type(conn: sqlite3.Connection) -> bool:
    """检测 entry_audit_events 的 event_type CHECK 是否包含 high_risk_resolved。"""
    row = conn.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'entry_audit_events'
        """
    ).fetchone()
    if row is None or row["sql"] is None:
        return False
    normalized = "".join(str(row["sql"]).lower().split())
    return "high_risk_resolved" in normalized


def _audit_events_has_bulk_reopen_type(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'entry_audit_events'
        """
    ).fetchone()
    if row is None or row["sql"] is None:
        return False
    normalized = "".join(str(row["sql"]).lower().split())
    return "bulk_reopen" in normalized


def _rebuild_audit_events(conn: sqlite3.Connection) -> None:
    """重建 entry_audit_events 以扩展 event_type CHECK（高风险 + 退回待确认）。"""
    conn.execute("ALTER TABLE entry_audit_events RENAME TO entry_audit_events__legacy")
    conn.execute(
        """
        CREATE TABLE entry_audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_type TEXT NOT NULL CHECK(event_type IN ('manual_edit','bulk_confirm','rule_applied','refund_linked','batch_revoked','high_risk_resolved','bulk_reopen')),
          ref_ledger_id INTEGER,
          ref_rule_id INTEGER,
          ref_batch_id INTEGER,
          detail TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        INSERT INTO entry_audit_events(
          id, event_type, ref_ledger_id, ref_rule_id, ref_batch_id, detail, created_at
        )
        SELECT
          id, event_type, ref_ledger_id, ref_rule_id, ref_batch_id, detail, created_at
        FROM entry_audit_events__legacy
        ORDER BY id ASC
        """
    )
    conn.execute("DROP TABLE entry_audit_events__legacy")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_created "
        "ON entry_audit_events(created_at DESC, id DESC)"
    )


def _migrate_v2_schema(conn: sqlite3.Connection) -> None:
    """把已初始化的 v2 库升级到当前 schema 版本（幂等，须在事务内调用）。

    旧 v2 库（无版本号或版本 < SCHEMA_VERSION）按版本阶梯升级：
    v3：source_transactions 补 raw_type、classification_rules 空模式 CHECK
    v4：refund_links.refund_source_id 唯一（清理多重链接脏数据）
    v5：entry_audit_events 事件类型扩展（high_risk_resolved）
    v6：classification_rules 增加 platform/direction 条件，修复脱敏商户规则；
        entry_audit_events 增加 bulk_reopen（退回待确认）
    """
    current = _current_schema_version(conn)
    if current is not None and current >= SCHEMA_VERSION:
        return
    _ensure_v2_columns(conn)
    if not _classification_rules_has_blank_check(conn):
        dropped = _rebuild_classification_rules(conn)
        if dropped > 0:
            conn.execute(
                """
                INSERT OR REPLACE INTO schema_meta(key, value)
                VALUES (?, ?)
                """,
                ("migration_dropped_blank_rules", str(dropped)),
            )
    if not _refund_links_has_source_unique(conn):
        cleaned = _rebuild_refund_links(conn)
        if cleaned > 0:
            conn.execute(
                """
                INSERT OR REPLACE INTO schema_meta(key, value)
                VALUES (?, ?)
                """,
                ("migration_cleaned_duplicate_refund_links", str(cleaned)),
            )
    repaired_masked = _repair_masked_counterparty_rules(conn)
    if repaired_masked > 0:
        conn.execute(
            """
            INSERT OR REPLACE INTO schema_meta(key, value)
            VALUES (?, ?)
            """,
            ("migration_repaired_masked_counterparty_rules", str(repaired_masked)),
        )
    if not _audit_events_has_high_risk_type(conn) or not _audit_events_has_bulk_reopen_type(conn):
        _rebuild_audit_events(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_meta(key, value)
        VALUES (?, ?)
        """,
        (SCHEMA_VERSION_KEY, str(SCHEMA_VERSION)),
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
        current_version = _current_schema_version(conn) if initialized else None
    backed_up = False
    needs_migration = initialized and (
        current_version is None or current_version < SCHEMA_VERSION
    )
    if has_legacy or needs_migration:
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
        _migrate_v2_schema(conn)
        conn.commit()
    return backed_up
