import sqlite3

import pytest

from app.db import init_db
from app.ledger_repo import (
    BATCH_STATUS_REVOKED,
    REVIEW_DISMISSED,
    REVIEW_RESOLVED,
    RULE_STATUS_ACTIVE,
    RULE_STATUS_DISABLED,
    add_audit_event,
    bump_rule_stats,
    create_classification_rule,
    create_import_batch,
    create_ledger_entry,
    enqueue_review,
    get_import_batch,
    get_ledger_entry,
    get_source_transaction,
    list_audit_events,
    list_classification_rules,
    list_import_batches,
    list_refund_links,
    list_review_queue,
    list_source_transactions,
    resolve_review,
    revoke_batch,
    update_batch_counts,
    update_ledger_entry,
    update_rule_status,
)
from app.settings import Settings


@pytest.fixture
def db(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "ledger.sqlite")
    init_db(settings)
    return settings.db_path


def _source(db, source_txn_id="S1", platform="alipay"):
    from app.ledger_repo import insert_source_transaction

    return insert_source_transaction(
        db,
        platform=platform,
        source_txn_id=source_txn_id,
        occurred_at="2026-07-01 12:00:00",
        amount_cents=2500,
        direction="expense",
        status_text="success",
        counterparty="某食堂",
        item_desc="午饭",
        batch_id=None,
        normalized_hash=f"hash-{source_txn_id}",
    )


# ── import_batches ──


def test_import_batch_create_and_read(db):
    batch_id = create_import_batch(
        db,
        file_name="alipay.csv",
        platform="alipay",
        file_fingerprint="fp-abc",
    )
    row = get_import_batch(db, batch_id)
    assert row["file_name"] == "alipay.csv"
    assert row["platform"] == "alipay"
    assert row["file_fingerprint"] == "fp-abc"
    assert row["status"] == "active"
    assert int(row["row_count"]) == 0


def test_import_batch_update_counts(db):
    batch_id = create_import_batch(
        db, file_name="w.csv", platform="wechat", file_fingerprint="fp"
    )
    assert update_batch_counts(
        db, batch_id, row_count=10, accepted_count=6, skipped_count=2, pending_count=2
    )
    row = get_import_batch(db, batch_id)
    assert int(row["row_count"]) == 10
    assert int(row["accepted_count"]) == 6
    assert int(row["skipped_count"]) == 2
    assert int(row["pending_count"]) == 2


def test_import_batch_revoke_once(db):
    batch_id = create_import_batch(
        db, file_name="a.csv", platform="alipay", file_fingerprint="fp"
    )
    assert revoke_batch(db, batch_id, note="重复导入")
    row = get_import_batch(db, batch_id)
    assert row["status"] == BATCH_STATUS_REVOKED
    assert row["revoke_note"] == "重复导入"
    assert not revoke_batch(db, batch_id)


def test_import_batch_list_ordering(db):
    b1 = create_import_batch(
        db, file_name="a.csv", platform="alipay", file_fingerprint="f1"
    )
    b2 = create_import_batch(
        db, file_name="b.csv", platform="wechat", file_fingerprint="f2"
    )
    ids = [row["id"] for row in list_import_batches(db)]
    assert ids == [b2, b1]


# ── source_transactions ──


def test_source_transaction_insert_dedup(db):
    source_id, created = _source(db, "S1")
    assert created is True
    duplicate_id, created_again = _source(db, "S1")
    assert created_again is False
    assert duplicate_id == source_id
    assert get_source_transaction(db, source_id)["counterparty"] == "某食堂"


def test_source_transaction_cross_platform_no_dedup(db):
    alipay_id, _ = _source(db, "S1", platform="alipay")
    wechat_id, created = _source(db, "S1", platform="wechat")
    assert created is True
    assert wechat_id != alipay_id


def test_source_transaction_list_filters(db):
    _source(db, "A", platform="alipay")
    _source(db, "B", platform="wechat")
    assert len(list_source_transactions(db)) == 2
    assert len(list_source_transactions(db, platform="alipay")) == 1
    assert len(list_source_transactions(db, platform="wechat")) == 1


def test_source_transaction_negative_amount_rejected(db):
    from app.ledger_repo import insert_source_transaction

    with pytest.raises(sqlite3.IntegrityError):
        insert_source_transaction(
            db,
            platform="alipay",
            source_txn_id="NEG",
            occurred_at="2026-07-01 12:00:00",
            amount_cents=-1,
            direction="expense",
            status_text="success",
            normalized_hash="h-neg",
        )


# ── ledger_entries ──


def test_ledger_entry_create_update(db):
    source_id, _ = _source(db, "S1")
    entry_id = create_ledger_entry(
        db,
        entry_type="consumption",
        amount_cents=2500,
        category="日常三餐",
        txn_date="2026-07-01",
        source_transaction_id=source_id,
        note="午饭",
    )
    row = get_ledger_entry(db, entry_id)
    assert row["entry_type"] == "consumption"
    assert int(row["amount_cents"]) == 2500
    assert row["category"] == "日常三餐"
    assert int(row["manual_edited"]) == 0

    assert update_ledger_entry(
        db,
        entry_id,
        entry_type="consumption",
        amount_cents=3000,
        category="日常娱乐",
        txn_date="2026-07-02",
        note="改",
    )
    updated = get_ledger_entry(db, entry_id)
    assert int(updated["amount_cents"]) == 3000
    assert updated["category"] == "日常娱乐"
    assert int(updated["manual_edited"]) == 1


def test_ledger_entry_invalid_type_rejected(db):
    with pytest.raises(sqlite3.IntegrityError):
        create_ledger_entry(
            db,
            entry_type="mystery",
            amount_cents=100,
            txn_date="2026-07-01",
        )


# ── review_queue ──


def test_review_queue_lifecycle(db):
    source_id, _ = _source(db, "S1")
    review_id = enqueue_review(
        db,
        source_transaction_id=source_id,
        reason="人际转账，需人工确认",
        priority=5,
        suggested_category="",
        suggested_type="",
    )
    pending = list_review_queue(db)
    assert len(pending) == 1
    assert pending[0]["reason"] == "人际转账，需人工确认"
    assert int(pending[0]["priority"]) == 5

    entry_id = create_ledger_entry(
        db,
        entry_type="income",
        amount_cents=2500,
        txn_date="2026-07-01",
        source_transaction_id=source_id,
    )
    assert resolve_review(db, review_id, resolved_ledger_id=entry_id)
    assert list_review_queue(db, status=REVIEW_RESOLVED)
    assert not resolve_review(db, review_id, resolved_ledger_id=entry_id)


def test_review_queue_dismiss(db):
    source_id, _ = _source(db, "S1")
    review_id = enqueue_review(
        db, source_transaction_id=source_id, reason="重复", priority=1
    )
    assert resolve_review(
        db, review_id, resolved_ledger_id=None, status=REVIEW_DISMISSED
    )
    assert not list_review_queue(db)
    assert len(list_review_queue(db, status=REVIEW_DISMISSED)) == 1


def test_review_queue_priority_ordering(db):
    low_id, _ = _source(db, "LOW")
    high_id, _ = _source(db, "HIGH")
    enqueue_review(db, source_transaction_id=low_id, reason="低", priority=1)
    enqueue_review(db, source_transaction_id=high_id, reason="高", priority=5)
    rows = list_review_queue(db)
    assert rows[0]["reason"] == "高"
    assert rows[1]["reason"] == "低"


# ── refund_links ──


def test_refund_link_through_service(db):
    """退款关联只经受约束服务（link_refund_to_ledger），公开低层入口已封闭。"""
    from app.refunds.linking import link_refund_to_ledger

    refund_source, _ = _source(db, "REFUND")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE source_transactions SET status_text = '退款成功' WHERE id = ?",
            (refund_source,),
        )
    entry_id = create_ledger_entry(
        db,
        entry_type="consumption",
        amount_cents=2500,
        category="日常三餐",
        txn_date="2026-07-01",
    )
    enqueue_review(
        db,
        source_transaction_id=refund_source,
        reason="refund_pending",
        priority=5,
    )
    result = link_refund_to_ledger(db, refund_source, entry_id)
    links = list_refund_links(db, original_ledger_id=entry_id)
    assert len(links) == 1
    assert links[0]["id"] == result.refund_link_id
    assert int(links[0]["refund_amount_cents"]) == 2500


def test_no_public_low_level_refund_write(db):
    """复审 P1：公开低层 link_refund 写入入口必须不存在。"""
    import app.ledger_repo as repo

    assert not hasattr(repo, "link_refund")


# ── classification_rules ──


def test_classification_rule_crud(db):
    rule_id = create_classification_rule(
        db,
        match_field="counterparty",
        match_pattern="某食堂",
        target_type="consumption",
        target_category="日常三餐",
    )
    rows = list_classification_rules(db)
    assert len(rows) == 1
    assert rows[0]["status"] == "observing"
    assert rows[0]["target_category"] == "日常三餐"

    # 观察期不可直跳 active（状态机），必须经 promote_rule
    assert not update_rule_status(db, rule_id, RULE_STATUS_ACTIVE)
    from app.decisions.confirm import promote_rule

    assert promote_rule(db, rule_id)
    assert list_classification_rules(db, status=RULE_STATUS_ACTIVE)
    assert not list_classification_rules(db, status=RULE_STATUS_DISABLED)
    # 停用后重新启用允许
    assert update_rule_status(db, rule_id, RULE_STATUS_DISABLED)
    assert update_rule_status(db, rule_id, RULE_STATUS_ACTIVE)
    assert not update_rule_status(db, rule_id + 99, RULE_STATUS_ACTIVE)


def test_classification_rule_stats(db):
    rule_id = create_classification_rule(
        db,
        match_field="item_desc",
        match_pattern="午饭",
        target_type="consumption",
    )
    assert bump_rule_stats(db, rule_id, confirmed=False)
    assert bump_rule_stats(db, rule_id, confirmed=True)
    row = list_classification_rules(db)[0]
    assert int(row["hit_count"]) == 1
    assert int(row["confirm_count"]) == 1


# ── audit events ──


def test_audit_event_add_and_list(db):
    add_audit_event(db, event_type="manual_edit", ref_ledger_id=1, detail="改分类")
    add_audit_event(db, event_type="rule_applied", ref_rule_id=3, detail="自动入账")
    events = list_audit_events(db)
    assert len(events) == 2
    assert {row["event_type"] for row in events} == {"manual_edit", "rule_applied"}
    assert events[0]["ref_rule_id"] == 3
