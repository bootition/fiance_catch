"""阶段 4 测试：安全批次撤销（规格 §3.6/§6）。"""

import pytest

from app.db import init_db
from app.decisions.confirm import confirm_group, group_review_items
from app.decisions.constants import CATEGORY_DAILY_MEALS, TYPE_CONSUMPTION
from app.decisions.engine import process_batch
from app.importing.service import import_file
from app.ledger_repo import (
    get_import_batch,
    list_ledger_entries,
    list_review_queue,
    list_source_transactions,
    update_ledger_entry,
)
from app.refunds.linking import link_refund_to_ledger
from app.refunds.matching import find_refund_candidates
from app.revoke import revoke_batch
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


def _confirm_all_unmatched(db, category=CATEGORY_DAILY_MEALS):
    while True:
        groups = group_review_items(db)
        if not groups:
            break
        g = groups[0]
        confirm_group(
            db, g.counterparty, g.platform,
            entry_type=TYPE_CONSUMPTION, category=category,
        )


def test_revoke_clean_batch_deletes_all(db, tmp_path):
    rows = [
        "2026-07-31 19:21:36,日用百货,信美佳超市,/,消费,支出,25.00,余额宝,交易成功,TXN-REV1,,",
        "2026-07-31 23:58:26,转账红包,鸿,188******65,7月份闲鱼收入,收入,27.38,账户余额,交易成功,TXN-REV2,,",
    ]
    result = _import(db, tmp_path, rows)
    process_batch(db, result.batch_id)

    revoke = revoke_batch(db, result.batch_id)
    assert revoke.blocked == []
    assert revoke.deleted_sources == 2
    assert revoke.deleted_reviews == 2
    assert revoke.deleted_ledger == 0

    batch = get_import_batch(db, result.batch_id)
    assert batch["status"] == "revoked"
    assert int(batch["pending_count"]) == 0
    assert list_source_transactions(db) == []
    assert list_review_queue(db) == []


def test_revoke_removes_auto_posted_ledger_but_blocks_edited(db, tmp_path):
    from app.decisions.constants import CATEGORY_TRANSPORT
    from app.ledger_repo import create_classification_rule
    from app.decisions.confirm import promote_rule

    # 规则自动入账两笔 + 一笔待确认
    create_classification_rule(
        db,
        match_field="counterparty",
        match_pattern="滴滴",
        target_type=TYPE_CONSUMPTION,
        target_category=CATEGORY_TRANSPORT,
    )
    promote_rule(db, 1)
    rows = [
        "2026-07-31 19:00:00,交通出行,滴滴出行,/,打车,支出,20.00,余额宝,交易成功,TXN-REV3,,",
        "2026-07-31 18:00:00,交通出行,滴滴出行,/,打车,支出,15.00,余额宝,交易成功,TXN-REV4,,",
        "2026-07-31 12:00:00,餐饮美食,某食堂,/,午饭,支出,10.00,余额宝,交易成功,TXN-REV5,,",
    ]
    result = _import(db, tmp_path, rows)
    process_batch(db, result.batch_id)
    _confirm_all_unmatched(db)  # 某食堂入账
    assert len(list_ledger_entries(db)) == 3

    # 人工编辑其中一笔自动入账记录 → 成为阻塞项
    auto = [e for e in list_ledger_entries(db) if e["source_transaction_id"]]
    edited = auto[0]
    update_ledger_entry(
        db,
        edited["id"],
        entry_type=TYPE_CONSUMPTION,
        amount_cents=999,
        category="改",
        txn_date="2026-07-31",
        note="手工修改",
    )

    revoke = revoke_batch(db, result.batch_id)
    kinds = {b.reason for b in revoke.blocked}
    assert "manual_edited" in kinds
    assert revoke.deleted_ledger == 2  # 未编辑的两笔删除
    remaining = list_ledger_entries(db)
    assert len(remaining) == 1
    assert remaining[0]["id"] == edited["id"]


def test_revoke_blocks_refund_linked_items(db, tmp_path):
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,40.00,余额宝,交易成功,TXN-RV-C1,,",
        "2026-07-11 10:00:00,日用百货,某店,/,退款-消费,不计收支,40.00,余额宝,退款成功,TXN-RV-C1_RM1,,",
    ]
    result = _import(db, tmp_path, rows)
    process_batch(db, result.batch_id)
    _confirm_all_unmatched(db)
    from app.ledger_repo import list_source_transactions

    refund_source = next(
        s for s in list_source_transactions(db) if s["source_txn_id"] == "TXN-RV-C1_RM1"
    )
    ledger_id = list_ledger_entries(db)[0]["id"]
    link_refund_to_ledger(db, refund_source["id"], ledger_id)

    revoke = revoke_batch(db, result.batch_id)
    blocked_reasons = {b.reason for b in revoke.blocked}
    assert "refund_linked" in blocked_reasons
    # 原消费保留，退款来源流水保留
    remaining = list_source_transactions(db)
    assert {s["source_txn_id"] for s in remaining} == {"TXN-RV-C1", "TXN-RV-C1_RM1"}
    assert len(list_ledger_entries(db)) == 1
    # 其余（待确认等）已删除
    assert revoke.deleted_reviews == 0  # 退款项已 resolved，无 pending 可删


def test_revoke_already_revoked_raises(db, tmp_path):
    result = _import(
        db,
        tmp_path,
        ["2026-07-31 19:21:36,日用百货,某店,/,消费,支出,25.00,余额宝,交易成功,TXN-REV6,,"],
    )
    process_batch(db, result.batch_id)
    revoke_batch(db, result.batch_id)
    with pytest.raises(ValueError, match="already revoked"):
        revoke_batch(db, result.batch_id)


def test_revoke_missing_batch_raises(db, tmp_path):
    with pytest.raises(ValueError, match="not found"):
        revoke_batch(db, 9999)


def test_revoke_unknown_batch_pending_unchanged(db, tmp_path):
    """撤销一个批次不影响其他批次的待确认项。"""
    r1 = _import(
        db,
        tmp_path,
        ["2026-07-31 19:00:00,餐饮美食,某食堂,/,午饭,支出,10.00,余额宝,交易成功,TXN-K1,,"],
        name="a.csv",
    )
    r2 = _import(
        db,
        tmp_path,
        ["2026-07-30 19:00:00,餐饮美食,另一食堂,/,午饭,支出,12.00,余额宝,交易成功,TXN-K2,,"],
        name="b.csv",
    )
    process_batch(db, r1.batch_id)
    process_batch(db, r2.batch_id)
    revoke_batch(db, r1.batch_id)
    assert get_import_batch(db, r2.batch_id)["status"] == "active"
    assert int(get_import_batch(db, r2.batch_id)["pending_count"]) == 1
    assert len(list_review_queue(db)) == 1
