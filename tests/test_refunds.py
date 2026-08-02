"""阶段 4 测试：退款候选匹配、人工关联、跨期回写（规格 §7.4）。"""

import pytest

from app.db import init_db
from app.decisions.constants import CATEGORY_DAILY_MEALS, TYPE_CONSUMPTION
from app.decisions.engine import process_batch
from app.importing.service import import_file
from app.ledger_repo import (
    get_import_batch,
    get_ledger_entry,
    list_audit_events,
    list_consumption_with_refunds,
    list_ledger_entries,
    list_refund_links,
    list_review_queue,
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


def _source_id(db, txn_id):
    from app.ledger_repo import list_source_transactions

    for s in list_source_transactions(db):
        if s["source_txn_id"] == txn_id:
            return s["id"]
    raise AssertionError(f"source not found: {txn_id}")


def _confirm_all_unmatched(db, category=CATEGORY_DAILY_MEALS):
    """把全部未命中待确认按商户批量确认为消费（简化测试铺设）。"""
    from app.decisions.confirm import confirm_group, group_review_items

    while True:
        groups = group_review_items(db)
        if not groups:
            break
        g = groups[0]
        confirm_group(
            db, g.counterparty, g.platform,
            entry_type=TYPE_CONSUMPTION, category=category,
        )


# ── 候选匹配 ──


def _setup_consumption_and_refund(db, tmp_path):
    rows = [
        # 原消费：拼多多订单，商户单号 XP1626072801100159132957003010
        "2026-07-28 01:15:37,日用百货,拼多多平台商户,pdd***@yiran.com,商户单号XP1626072801100159132957003010,支出,40.00,余额宝,交易成功,TXN-C1,,",
        # 无关消费（同金额不同商户，时间更近）——测试打分
        "2026-07-30 12:00:00,日用百货,某超市,/,消费,支出,40.00,余额宝,交易成功,TXN-C2,,",
        # 退款行（对应 TXN-C1）
        "2026-07-30 17:12:03,日用百货,拼多多平台商户,pdd***@yiran.com,退款-商户单号XP1626072801100159132957003010,不计收支,40.00,余额宝,退款成功,TXN-R1_RM001,,",
    ]
    result = _import(db, tmp_path, rows)
    process_batch(db, result.batch_id)
    _confirm_all_unmatched(db)
    return result


def test_refund_candidates_merchant_id_scored(db, tmp_path):
    _setup_consumption_and_refund(db, tmp_path)
    refund_source_id = _source_id(db, "TXN-R1_RM001")
    candidates = find_refund_candidates(db, refund_source_id)
    assert len(candidates) >= 2
    # 商户单号匹配（90 分）优先于同金额超市（60 分）
    assert candidates[0].match_reason == "商户单号匹配"
    assert candidates[0].score == 90
    assert candidates[1].score == 60


def test_refund_candidates_original_txn_prefix(db, tmp_path):
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,20.00,余额宝,交易成功,TXN-ABC123,,",
        "2026-07-11 10:00:00,日用百货,某店,/,退款-消费,不计收支,20.00,余额宝,退款成功,TXN-ABC123_RM999,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    refund_source_id = _source_id(db, "TXN-ABC123_RM999")
    candidates = find_refund_candidates(db, refund_source_id)
    assert candidates[0].match_reason == "原交易订单号匹配"
    assert candidates[0].score == 100


def test_refund_candidates_none_when_no_match(db, tmp_path):
    """金额不同且无任何订单引用 → 无候选，退款停待确认。"""
    rows = [
        "2026-06-01 10:00:00,日用百货,旧商户,/,消费,支出,50.00,余额宝,交易成功,TXN-OLD1,,",
        "2026-07-30 10:00:00,日用百货,新商户,/,退款,不计收支,30.00,余额宝,退款成功,TXN-NEW1,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    refund_source_id = _source_id(db, "TXN-NEW1")
    candidates = find_refund_candidates(db, refund_source_id)
    assert candidates == []
    # 找不到原消费：退款停留在待确认（不默认作为收入）
    queue = list_review_queue(db)
    refund_items = [r for r in queue if r["reason"] == "refund_pending"]
    assert len(refund_items) == 1


# ── 人工关联与跨期回写 ──


def test_link_refund_records_link_and_syncs(db, tmp_path):
    result = _setup_consumption_and_refund(db, tmp_path)
    refund_source_id = _source_id(db, "TXN-R1_RM001")
    candidates = find_refund_candidates(db, refund_source_id)
    ledger_id = candidates[0].ledger_id

    link_result = link_refund_to_ledger(db, refund_source_id, ledger_id)
    assert link_result.net_cost_cents == 0  # 40 - 40
    assert link_result.review_id is not None

    links = list_refund_links(db, original_ledger_id=ledger_id)
    assert len(links) == 1
    assert int(links[0]["refund_amount_cents"]) == 4000
    # 待确认已关闭
    assert list_review_queue(db) == []
    # 审计
    assert any(e["event_type"] == "refund_linked" for e in list_audit_events(db))
    # 批次计数同步
    batch = get_import_batch(db, result.batch_id)
    assert int(batch["pending_count"]) == 0


def test_link_refund_cross_period_writeback(db, tmp_path):
    """跨期退款：6 月消费、7 月退款，净成本归入原消费周期（6 月）。"""
    rows = [
        "2026-06-20 10:00:00,日用百货,某店,/,消费,支出,100.00,余额宝,交易成功,TXN-JUN1,,",
        "2026-07-05 10:00:00,日用百货,某店,/,退款-消费,不计收支,100.00,余额宝,退款成功,TXN-JUN1_RM777,,",
    ]
    result = _import(db, tmp_path, rows)
    process_batch(db, result.batch_id)
    _confirm_all_unmatched(db)
    refund_source_id = _source_id(db, "TXN-JUN1_RM777")
    candidates = find_refund_candidates(db, refund_source_id)
    ledger_id = candidates[0].ledger_id
    assert get_ledger_entry(db, ledger_id)["txn_date"] == "2026-06-20"

    link_refund_to_ledger(db, refund_source_id, ledger_id)

    # 6 月周期：净成本 0；7 月周期：无消费
    june = list_consumption_with_refunds(db, start="2026-06-01", end="2026-06-30")
    july = list_consumption_with_refunds(db, start="2026-07-01", end="2026-07-31")
    assert len(june) == 1 and june[0]["net_cost_cents"] == 0
    assert june[0]["refunded_cents"] == 10000
    assert july == []


def test_link_refund_exceeds_original_amount_rejected(db, tmp_path):
    """退款总额超过原消费金额时拒绝（部分退款可多次关联但不可超额）。"""
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,20.00,余额宝,交易成功,TXN-EX1,,",
        "2026-07-11 10:00:00,日用百货,某店,/,退款-消费,不计收支,20.00,余额宝,退款成功,TXN-EX2,,",
        "2026-07-12 10:00:00,日用百货,某店,/,退款-消费,不计收支,10.00,余额宝,退款成功,TXN-EX3,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    ledger_id = list_ledger_entries(db)[0]["id"]

    link_refund_to_ledger(db, _source_id(db, "TXN-EX2"), ledger_id)  # 20 = 20 OK
    with pytest.raises(ValueError, match="exceeds"):
        link_refund_to_ledger(db, _source_id(db, "TXN-EX3"), ledger_id)  # 20+10 > 20
    assert len(list_refund_links(db, original_ledger_id=ledger_id)) == 1
    # 被拒的退款仍待确认
    queue = [r for r in list_review_queue(db) if r["reason"] == "refund_pending"]
    assert len(queue) == 1


def test_link_refund_multiple_partial_links(db, tmp_path):
    """部分退款可多次关联，总额不超过原金额。"""
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,50.00,余额宝,交易成功,TXN-PA1,,",
        "2026-07-11 10:00:00,日用百货,某店,/,退款-消费,不计收支,20.00,余额宝,退款成功,TXN-PA2,,",
        "2026-07-12 10:00:00,日用百货,某店,/,退款-消费,不计收支,30.00,余额宝,退款成功,TXN-PA3,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    _confirm_all_unmatched(db)
    ledger_id = list_ledger_entries(db)[0]["id"]

    r1 = link_refund_to_ledger(db, _source_id(db, "TXN-PA2"), ledger_id)
    assert r1.net_cost_cents == 3000
    r2 = link_refund_to_ledger(db, _source_id(db, "TXN-PA3"), ledger_id)
    assert r2.net_cost_cents == 0
    with pytest.raises(ValueError, match="exceeds"):
        link_refund_to_ledger(db, _source_id(db, "TXN-PA2"), ledger_id)  # 重复关联 20 会超
    assert len(list_refund_links(db, original_ledger_id=ledger_id)) == 2


def test_link_refund_rejects_non_consumption(db, tmp_path):
    rows = [
        "2026-07-31 23:59:17,投资理财,余额宝,yue***@csfunds.com.cn,余额宝-单次转入,不计收支,86.73,账户余额,交易成功,TXN-TF1,,",
        "2026-07-30 17:12:03,日用百货,某店,/,退款-消费,不计收支,10.00,余额宝,退款成功,TXN-RX1,,",
    ]
    _import(db, tmp_path, rows)
    process_batch(db, 1)
    # 调拨已自动入账为 transfer；退款在队列
    ledger = list_ledger_entries(db)[0]
    assert ledger["entry_type"] == "transfer"
    refund_source_id = _source_id(db, "TXN-RX1")
    with pytest.raises(ValueError, match="consumption"):
        link_refund_to_ledger(db, refund_source_id, ledger["id"])
