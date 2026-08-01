import sqlite3

import pytest

from app.db import init_db
from app.settings import Settings

CORE_TABLES = (
    "schema_meta",
    "import_batches",
    "source_transactions",
    "ledger_entries",
    "review_queue",
    "refund_links",
    "classification_rules",
    "entry_audit_events",
)


def _init(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "ledger.sqlite")
    init_db(settings)
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row["name"] for row in rows}


def _insert_source(conn, source_txn_id, *, platform="alipay", hash_value="h"):
    conn.execute(
        """
        INSERT INTO source_transactions(
          platform, source_txn_id, occurred_at, amount_cents,
          direction, status_text, normalized_hash
        )
        VALUES (?, ?, '2026-07-01', 100, 'expense', 'success', ?)
        """,
        (platform, source_txn_id, hash_value),
    )


def test_new_schema_tables_created(tmp_path):
    conn = _init(tmp_path)
    try:
        names = _table_names(conn)
        for table in CORE_TABLES:
            assert table in names, f"missing table: {table}"
    finally:
        conn.close()


def test_source_transactions_unique_per_platform(tmp_path):
    conn = _init(tmp_path)
    try:
        _insert_source(conn, "A1")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source(conn, "A1")
        _insert_source(conn, "A1", platform="wechat")
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM source_transactions"
        ).fetchone()["c"]
        assert int(count) == 2
    finally:
        conn.close()


def test_source_transactions_direction_check(tmp_path):
    conn = _init(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO source_transactions(
                  platform, source_txn_id, occurred_at, amount_cents,
                  direction, status_text, normalized_hash
                )
                VALUES ('alipay', 'A2', '2026-07-01', 100, 'sideways', 'success', 'h')
                """
            )
    finally:
        conn.close()


def test_import_batches_status_check(tmp_path):
    conn = _init(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO import_batches(file_name, platform, file_fingerprint, status)
                VALUES ('x.csv', 'alipay', 'fp', 'weird')
                """
            )
    finally:
        conn.close()


def test_ledger_entries_type_check(tmp_path):
    conn = _init(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO ledger_entries(entry_type, amount_cents, txn_date)
                VALUES ('mystery', 100, '2026-07-01')
                """
            )
    finally:
        conn.close()


def test_review_queue_status_check(tmp_path):
    conn = _init(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO review_queue(source_transaction_id, reason, status)
                VALUES (1, 'test', 'weird')
                """
            )
    finally:
        conn.close()


def test_classification_rules_status_and_field_check(tmp_path):
    conn = _init(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO classification_rules(
                  match_field, match_pattern, target_type, status
                )
                VALUES ('counterparty', 'x', 'consumption', 'weird')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO classification_rules(
                  match_field, match_pattern, target_type
                )
                VALUES ('unknown_field', 'x', 'consumption')
                """
            )
    finally:
        conn.close()


def test_audit_events_type_check(tmp_path):
    conn = _init(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO entry_audit_events(event_type)
                VALUES ('weird')
                """
            )
    finally:
        conn.close()


def test_ledger_v2_marker_recorded(tmp_path):
    conn = _init(tmp_path)
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'ledger_v2_init_at'"
        ).fetchone()
        assert row is not None and str(row["value"]).strip() != ""
    finally:
        conn.close()


def test_new_schema_indexes_created(tmp_path):
    conn = _init(tmp_path)
    try:
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        expected = {
            "idx_source_transactions_occurred_at",
            "idx_source_transactions_batch_id",
            "idx_ledger_entries_txn_date",
            "idx_ledger_entries_source_txn",
            "idx_ledger_entries_batch_id",
            "idx_review_queue_status_priority",
            "idx_refund_links_original",
            "idx_classification_rules_status",
            "idx_audit_events_created",
        }
        for index in expected:
            assert index in indexes, f"missing index: {index}"
    finally:
        conn.close()


def test_legacy_tables_removed_from_active_db(tmp_path):
    conn = _init(tmp_path)
    try:
        names = _table_names(conn)
        for table in ("transactions", "accounts", "import_sessions", "import_rows", "category_rules"):
            assert table not in names, f"legacy table still present: {table}"
    finally:
        conn.close()
