"""阶段 4 红队审查回归测试（docs/reports/09_phase4_red_team_review_2026-08-01.md）。"""

import sqlite3

import pytest

from app.db import init_db
from app.decisions.confirm import confirm_group, group_review_items
from app.decisions.constants import CATEGORY_DAILY_MEALS, TYPE_CONSUMPTION
from app.decisions.engine import process_batch
from app.importing.service import import_file
from app.ledger_repo import (
    get_import_batch,
    list_audit_events,
    list_ledger_entries,
    list_refund_links,
    list_review_queue,
    list_source_transactions,
)
from app.refunds.linking import link_refund_to_ledger
from app.refunds.matching import find_refund_candidates
from app.settings import Settings

ALIPAY_HEADER = (
    "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,"
    "收/付款方式,交易状态,交易订单号,商家订单号,备注"
)


@pytest.fixture
def db(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "ledger.sqlite")
    init_db(settings)
    return settings.db_path


def _import(db, tmp_path, rows, name="alipay.csv"):
    path = tmp_path / name
    path.write_bytes(
        "\n".join(["----导出信息----", ALIPAY_HEADER] + rows).encode("gb18030")
    )
    return import_file(db, path, "alipay")


def _confirm_all_unmatched(db):
    while True:
        groups = group_review_items(db)
        if not groups:
            break
        g = groups[0]
        confirm_group(
            db, g.counterparty, g.platform,
            entry_type=TYPE_CONSUMPTION, category=CATEGORY_DAILY_MEALS,
        )


def _source_id(db, txn_id):
    for s in list_source_transactions(db):
        if s["source_txn_id"] == txn_id:
            return s["id"]
    raise AssertionError(f"source not found: {txn_id}")


def _assert_no_state_change(db, before_links, before_queue, before_audit):
    """失败关联不得改变 refund_links、待确认队列或审计记录。"""
    assert len(list_refund_links(db)) == before_links
    assert len(list_review_queue(db)) == before_queue
    assert len(list_audit_events(db)) == before_audit


# ── P1-A：非退款来源拒绝 ──


def test_link_rejects_normal_income_source(db, tmp_path):
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,20.00,余额宝,交易成功,TXN-FAKE-1,,",
        "2026-07-11 10:00:00,转账红包,鸿,188******65,闲鱼收入,收入,10.00,账户余额,交易成功,TXN-FAKE-2,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    ledger_id = list_ledger_entries(db)[0]["id"]
    income_source = _source_id(db, "TXN-FAKE-2")

    before = (len(list_refund_links(db)), len(list_review_queue(db)), len(list_audit_events(db)))
    with pytest.raises(ValueError, match="not a refund"):
        link_refund_to_ledger(db, income_source, ledger_id)
    _assert_no_state_change(db, *before)


def test_link_rejects_normal_expense_source(db, tmp_path):
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,20.00,余额宝,交易成功,TXN-FE-1,,",
        "2026-07-11 10:00:00,日用百货,某店,/,另一笔消费,支出,10.00,余额宝,交易成功,TXN-FE-2,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    entries = list_ledger_entries(db)
    expense_source = _source_id(db, "TXN-FE-2")

    before = (len(list_refund_links(db)), len(list_review_queue(db)), len(list_audit_events(db)))
    with pytest.raises(ValueError, match="not a refund"):
        link_refund_to_ledger(db, expense_source, entries[0]["id"])
    _assert_no_state_change(db, *before)


def test_link_rejects_transfer_source(db, tmp_path):
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,20.00,余额宝,交易成功,TXN-FT-1,,",
        "2026-07-11 10:00:00,投资理财,余额宝,yue***@csfunds.com.cn,余额宝-单次转入,不计收支,10.00,账户余额,交易成功,TXN-FT-2,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    entries = list_ledger_entries(db)
    transfer = next(e for e in entries if e["entry_type"] == "transfer")
    transfer_source = _source_id(db, "TXN-FT-2")

    before = (len(list_refund_links(db)), len(list_review_queue(db)), len(list_audit_events(db)))
    with pytest.raises(ValueError, match="not a refund"):
        link_refund_to_ledger(db, transfer_source, entries[0]["id"])
    _assert_no_state_change(db, *before)


def test_link_rejects_source_without_pending_review(db, tmp_path):
    """退款状态但待办已处理（如已 dismissed）→ 拒绝。"""
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,20.00,余额宝,交易成功,TXN-NR-1,,",
        "2026-07-11 10:00:00,日用百货,某店,/,退款-消费,不计收支,20.00,余额宝,退款成功,TXN-NR-2,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    # 手工关闭退款待办（模拟已处理）
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE review_queue SET status='dismissed' WHERE reason='refund_pending'")
    ledger_id = list_ledger_entries(db)[0]["id"]
    refund_source = _source_id(db, "TXN-NR-2")

    before = (len(list_refund_links(db)), len(list_review_queue(db)), len(list_audit_events(db)))
    with pytest.raises(ValueError, match="no pending refund review"):
        link_refund_to_ledger(db, refund_source, ledger_id)
    _assert_no_state_change(db, *before)


# ── P1-B：同一退款不可重复关联 ──


def test_link_same_refund_to_second_consumption_rejected(db, tmp_path):
    """红队复现：一笔 ¥10 退款关联两笔 ¥20 消费 → 第二次拒绝。"""
    rows = [
        "2026-07-10 10:00:00,日用百货,商店A,/,消费,支出,20.00,余额宝,交易成功,TXN-DUP-1,,",
        "2026-07-10 11:00:00,日用百货,商店B,/,消费,支出,20.00,余额宝,交易成功,TXN-DUP-2,,",
        "2026-07-11 10:00:00,日用百货,商店A,/,退款-消费,不计收支,10.00,余额宝,退款成功,TXN-DUP-3,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    entries = list_ledger_entries(db)
    refund_source = _source_id(db, "TXN-DUP-3")

    r1 = link_refund_to_ledger(db, refund_source, entries[0]["id"])
    assert r1.refund_link_id > 0
    with pytest.raises(ValueError, match="already linked"):
        link_refund_to_ledger(db, refund_source, entries[1]["id"])
    assert len(list_refund_links(db)) == 1


def test_refund_links_schema_has_source_unique(db, tmp_path):
    """schema 层兜底：refund_source_id 唯一约束存在。"""
    from app.db import init_db as _init

    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "ledger.sqlite")
    _init(settings)
    with sqlite3.connect(str(settings.db_path)) as conn:
        assert _has_source_unique(conn)


def _has_source_unique(conn):
    for index in conn.execute("PRAGMA index_list(refund_links)").fetchall():
        if not int(index[2]):  # index_list: (seq, name, unique, origin, partial)
            continue
        cols = conn.execute(f"PRAGMA index_info({index[1]})").fetchall()
        if any(col[2] == "refund_source_id" for col in cols):
            return True
    return False


# ── P2：无可退余额消费不出现在候选 ──


def test_candidates_exclude_fully_refunded_consumption(db, tmp_path):
    """红队复现：¥10 消费已全额退款后，不再作为后续退款候选。"""
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,10.00,余额宝,交易成功,TXN-FR-1,,",
        "2026-07-11 10:00:00,日用百货,某店,/,退款-消费,不计收支,10.00,余额宝,退款成功,TXN-FR-1_RM1,,",
        "2026-07-12 10:00:00,日用百货,某店,/,退款-消费,不计收支,5.00,余额宝,退款成功,TXN-FR-2,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    ledger_id = list_ledger_entries(db)[0]["id"]
    link_refund_to_ledger(db, _source_id(db, "TXN-FR-1_RM1"), ledger_id)

    # 第二笔退款查找候选：该消费已全额退款，不应出现
    candidates = find_refund_candidates(db, _source_id(db, "TXN-FR-2"))
    assert candidates == []


# ── 迁移：旧 v3 库多重链接脏数据清理 ──


def test_migration_v4_cleans_duplicate_refund_links(tmp_path):
    """v3 旧库含同一退款多链接脏数据 → 升级 v4 保留最早一条并记录。"""
    from app.migration_v2 import init_new_schema

    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "ledger.sqlite")
    init_db(settings)
    # 模拟 v3 旧形态：重建无唯一约束的 refund_links，插入两笔消费 + 一笔退款多链接
    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("DROP TABLE refund_links")
        conn.execute(
            """
            CREATE TABLE refund_links (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              refund_source_id INTEGER NOT NULL REFERENCES source_transactions(id) ON DELETE RESTRICT,
              original_ledger_id INTEGER NOT NULL REFERENCES ledger_entries(id) ON DELETE RESTRICT,
              refund_amount_cents INTEGER NOT NULL CHECK(refund_amount_cents >= 0),
              linked_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT INTO source_transactions(platform, source_txn_id, occurred_at, amount_cents, direction, status_text, normalized_hash) VALUES ('alipay','S1','2026-07-01 00:00:00',100,'neutral','退款成功','h1')"
        )
        conn.execute(
            "INSERT INTO ledger_entries(entry_type, amount_cents, txn_date) VALUES ('consumption', 100, '2026-07-01')"
        )
        conn.execute(
            "INSERT INTO ledger_entries(entry_type, amount_cents, txn_date) VALUES ('consumption', 100, '2026-07-02')"
        )
        conn.execute(
            "INSERT INTO refund_links(refund_source_id, original_ledger_id, refund_amount_cents) VALUES (1, 1, 100)"
        )
        conn.execute(
            "INSERT INTO refund_links(refund_source_id, original_ledger_id, refund_amount_cents) VALUES (1, 2, 100)"
        )
        conn.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
        conn.commit()

    init_db(settings)  # 触发 v4 迁移

    with sqlite3.connect(str(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert version["value"] == "6"
        links = conn.execute("SELECT * FROM refund_links").fetchall()
        assert len(links) == 1  # 保留最早一条
        assert int(links[0]["original_ledger_id"]) == 1
        cleaned = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'migration_cleaned_duplicate_refund_links'"
        ).fetchone()
        assert cleaned is not None and cleaned["value"] == "1"
        assert _has_source_unique(conn)
