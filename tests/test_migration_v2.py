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


def test_ensure_ledger_v2_backs_up_legacy_once(tmp_path):
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
    with sqlite3.connect(str(backups[0])) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()
        assert int(row["c"]) == 1


def test_init_db_rerun_does_not_create_second_backup(tmp_path):
    settings = _legacy_db(tmp_path)
    init_db(settings)
    init_db(settings)
    assert len(list(tmp_path.glob("ledger.sqlite-*.bak"))) == 1
