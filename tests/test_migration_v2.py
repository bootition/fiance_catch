import sqlite3
from pathlib import Path

import pytest

from app.db import init_db
from app.migration_v2 import (
    backup_db,
    ensure_ledger_v2,
    init_new_schema,
    new_schema_initialized,
)
from app.settings import Settings


def _settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, db_path=tmp_path / "ledger.sqlite")


def _legacy_db(tmp_path):
    settings = _settings(tmp_path)
    conn = sqlite3.connect(str(settings.db_path))
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
    conn.execute("INSERT INTO accounts(id, name) VALUES (1, 'Default')")
    conn.execute(
        """
        CREATE TABLE transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL DEFAULT 1,
          date TEXT NOT NULL,
          direction TEXT NOT NULL,
          amount_cents INTEGER NOT NULL,
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
        VALUES (1, '2026-06-01', 'expense', 1200, 'food', 'legacy')
        """
    )
    conn.commit()
    conn.close()
    return settings


def test_backup_db_creates_timestamped_backup(tmp_path):
    settings = _legacy_db(tmp_path)
    backup_path = backup_db(settings)
    assert backup_path.exists()
    assert backup_path.name.startswith("ledger.sqlite-")
    assert backup_path.name.endswith(".bak")
    assert backup_path != settings.db_path
    with sqlite3.connect(str(backup_path)) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        assert result[0] == "ok"
        row = conn.execute("SELECT category FROM transactions").fetchone()
        assert row[0] == "food"


def test_backup_db_missing_source_raises(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(FileNotFoundError):
        backup_db(settings)


def _table_names(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {row[0] for row in rows}


def test_ensure_ledger_v2_resets_legacy_and_backs_up_once(tmp_path):
    settings = _legacy_db(tmp_path)
    assert ensure_ledger_v2(settings) is True
    backups = list(tmp_path.glob("ledger.sqlite-*.bak"))
    assert len(backups) == 1
    assert ensure_ledger_v2(settings) is False
    assert len(list(tmp_path.glob("ledger.sqlite-*.bak"))) == 1
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        assert new_schema_initialized(conn)
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'ledger_v2_init_at'"
        ).fetchone()
        assert row is not None
        names = _table_names(settings.db_path)
        assert "transactions" not in names
        assert "accounts" not in names
        assert "import_sessions" not in names
        assert "import_rows" not in names
        assert "category_rules" not in names
    with sqlite3.connect(str(backups[0])) as conn:
        conn.row_factory = sqlite3.Row
        legacy_count = conn.execute(
            "SELECT COUNT(*) AS c FROM transactions"
        ).fetchone()["c"]
        assert int(legacy_count) == 1


def test_ensure_ledger_v2_fresh_db_no_backup(tmp_path):
    settings = _settings(tmp_path)
    init_db(settings)
    assert ensure_ledger_v2(settings) is False
    assert list(tmp_path.glob("ledger.sqlite-*.bak")) == []
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        assert new_schema_initialized(conn)


def test_ensure_ledger_v2_half_migrated_state_backs_up_before_drop(tmp_path):
    settings = _legacy_db(tmp_path)
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        init_new_schema(conn)
        conn.commit()
        assert new_schema_initialized(conn)
    assert ensure_ledger_v2(settings) is True
    backups = list(tmp_path.glob("ledger.sqlite-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(str(backups[0])) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()
        assert int(row["c"]) == 1
    assert "transactions" not in _table_names(settings.db_path)


def test_init_new_schema_idempotent(tmp_path):
    settings = _settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(settings.db_path)) as conn:
        init_new_schema(conn)
        conn.commit()
        table_count = conn.execute(
            "SELECT COUNT(*) AS c FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
        init_new_schema(conn)
        conn.commit()
        table_count_again = conn.execute(
            "SELECT COUNT(*) AS c FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
        assert table_count == table_count_again


def test_init_db_legacy_path_creates_backup_and_new_schema(tmp_path):
    settings = _legacy_db(tmp_path)
    init_db(settings)
    backups = list(tmp_path.glob("ledger.sqlite-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        assert new_schema_initialized(conn)
        names = _table_names(settings.db_path)
        assert "transactions" not in names
        assert "accounts" not in names
    with sqlite3.connect(str(backups[0])) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()
        assert int(row["c"]) == 1


def test_init_db_rerun_does_not_create_second_backup(tmp_path):
    settings = _legacy_db(tmp_path)
    init_db(settings)
    init_db(settings)
    assert len(list(tmp_path.glob("ledger.sqlite-*.bak"))) == 1
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        assert new_schema_initialized(conn)
        names = _table_names(settings.db_path)
        assert "transactions" not in names
        assert "accounts" not in names
        assert "import_sessions" not in names


def test_backup_db_wal_mode_consistency(tmp_path):
    settings = _legacy_db(tmp_path)
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            INSERT INTO transactions(account_id, date, direction, amount_cents, category, note)
            VALUES (1, '2026-06-02', 'expense', 500, 'shopping', 'wal-row')
            """
        )
        conn.commit()
    backup_path = backup_db(settings)
    with sqlite3.connect(str(backup_path)) as conn:
        conn.row_factory = sqlite3.Row
        result = conn.execute("PRAGMA integrity_check").fetchone()
        assert result[0] == "ok"
        rows = conn.execute("SELECT category FROM transactions").fetchall()
        assert sorted(row["category"] for row in rows) == ["food", "shopping"]


def _downgrade_to_stage2_v2_schema(db_path):
    """把当前 v2 库改回阶段 2 旧形态（无 raw_type、规则表无 CHECK、无版本号）。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("ALTER TABLE source_transactions DROP COLUMN raw_type")
    conn.execute("DROP TABLE classification_rules")
    conn.execute(
        """
        CREATE TABLE classification_rules (
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
        )
        """
    )
    conn.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
    conn.commit()
    conn.close()


def test_init_db_upgrades_stage2_v2_schema(tmp_path):
    """阶段 2 遗留 v2 库经 init_db 升级：raw_type 列、规则 CHECK、版本号。"""
    settings = _settings(tmp_path)
    init_db(settings)
    _downgrade_to_stage2_v2_schema(settings.db_path)

    init_db(settings)

    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(source_transactions)").fetchall()
        }
        assert "raw_type" in cols
        rule_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(classification_rules)").fetchall()
        }
        assert "platform" in rule_cols
        assert "direction" in rule_cols
        rule_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='classification_rules'"
        ).fetchone()["sql"]
        assert "trim(match_pattern)<>''" in "".join(str(rule_sql).lower().split())
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert version is not None and version["value"] == "6"


def test_init_db_upgrade_idempotent(tmp_path):
    settings = _settings(tmp_path)
    init_db(settings)
    _downgrade_to_stage2_v2_schema(settings.db_path)
    init_db(settings)
    init_db(settings)
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert version["value"] == "6"


def test_upgraded_lib_supports_import_and_decisions(tmp_path):
    """升级后的库能正常导入与决策（含 raw_type 依赖路径）。"""
    from app.importing.service import import_file
    from app.decisions.engine import process_batch
    from app.ledger_repo import list_review_queue

    settings = _settings(tmp_path)
    init_db(settings)
    _downgrade_to_stage2_v2_schema(settings.db_path)
    init_db(settings)

    path = tmp_path / "upgrade.csv"
    path.write_bytes(
        (
            "----导出信息----\n"
            "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,"
            "收/付款方式,交易状态,交易订单号,商家订单号,备注\n"
            "2026-07-31 23:58:26,转账红包,鸿,188******65,7月份闲鱼收入,收入,27.38,"
            "账户余额,交易成功,TXN-UP1,,\n"
        ).encode("gb18030")
    )
    result = import_file(settings.db_path, path, "alipay")
    processed = process_batch(settings.db_path, result.batch_id)
    assert processed.queued == 1
    assert list_review_queue(settings.db_path)[0]["reason"] == "person_transfer"


def _add_stage2_blank_rule(db_path):
    """模拟阶段 2 合法写入的空模式规则（无 CHECK 的旧表允许）。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO classification_rules(
          match_field, match_pattern, target_type, target_category, status
        )
        VALUES ('counterparty', '', 'consumption', 'bad', 'active')
        """
    )
    conn.commit()
    conn.close()


def test_init_db_upgrade_with_blank_rule_isolated(tmp_path):
    """二次红队 P1：旧库含空模式规则时升级不失败，隔离并记录。"""
    settings = _settings(tmp_path)
    init_db(settings)
    _downgrade_to_stage2_v2_schema(settings.db_path)
    _add_stage2_blank_rule(settings.db_path)
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.execute(
            """
            INSERT INTO classification_rules(
              match_field, match_pattern, target_type, target_category, status
            )
            VALUES ('counterparty', '有效商户', 'consumption', '日常三餐', 'observing')
            """
        )
        conn.commit()

    init_db(settings)  # 不应抛异常

    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert version["value"] == "6"
        rules = conn.execute(
            "SELECT match_pattern, status FROM classification_rules ORDER BY id"
        ).fetchall()
        assert [r["match_pattern"] for r in rules] == ["有效商户"]  # 空规则被隔离
        dropped = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'migration_dropped_blank_rules'"
        ).fetchone()
        assert dropped is not None and dropped["value"] == "1"

    # 幂等：再次升级不重复隔离、不报错
    init_db(settings)
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rules = conn.execute("SELECT match_pattern FROM classification_rules").fetchall()
        assert [r["match_pattern"] for r in rules] == ["有效商户"]
        dropped = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'migration_dropped_blank_rules'"
        ).fetchone()
        assert dropped["value"] == "1"
