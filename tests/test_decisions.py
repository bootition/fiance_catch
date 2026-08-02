import datetime

import openpyxl
import pytest

from app.db import init_db
from app.decisions.confirm import confirm_group, group_review_items, promote_rule
from app.decisions.constants import (
    CATEGORY_DAILY_MEALS,
    CATEGORY_SIDE_INCOME,
    CATEGORY_TRANSPORT,
    TYPE_CONSUMPTION,
    TYPE_INCOME,
    TYPE_TRANSFER,
)
from app.decisions.engine import process_batch
from app.importing.service import import_file
from app.ledger_repo import (
    create_classification_rule,
    get_ledger_entry,
    list_audit_events,
    list_classification_rules,
    list_ledger_entries,
    list_review_queue,
    list_source_transactions,
)
from app.settings import Settings


@pytest.fixture
def db(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "ledger.sqlite")
    init_db(settings)
    return settings.db_path


def _alipay_file(tmp_path, rows, name="alipay.csv"):
    lines = ["----------------导出信息----------------", ALIPAY_HEADER] + rows
    path = tmp_path / name
    path.write_bytes("\n".join(lines).encode("gb18030"))
    return path


ALIPAY_HEADER = (
    "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,"
    "收/付款方式,交易状态,交易订单号,商家订单号,备注"
)


def _import_rows(db, tmp_path, rows, platform="alipay", name="alipay.csv"):
    if platform == "alipay":
        path = _alipay_file(tmp_path, rows, name)
        return import_file(db, path, "alipay")
    path = tmp_path / name
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["微信支付账单明细"])
    ws.append(["------------------微信支付账单明细列表------------------"])
    ws.append(["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)", "支付方式", "当前状态", "交易单号", "商户单号", "备注"])
    for r in rows:
        ws.append(r)
    wb.save(path)
    return import_file(db, path, "wechat")


def _list_ledger(db):
    return list_ledger_entries(db)


# ── 引擎基本路径 ──


def test_process_batch_auto_posts_active_rule(db, tmp_path):
    create_classification_rule(
        db,
        match_field="counterparty",
        match_pattern="信美佳",
        target_type=TYPE_CONSUMPTION,
        target_category=CATEGORY_DAILY_MEALS,
    )
    promote_rule(db, 1)
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 19:21:36,日用百货,信美佳超市,/,消费,支出,25.00,余额宝,交易成功,TXN-P1,,",
            "2026-07-31 12:00:00,日用百货,未知商户,/,消费,支出,10.00,余额宝,交易成功,TXN-P2,,",
        ],
    )
    processed = process_batch(db, result.batch_id)
    assert processed.posted == 1
    assert processed.queued == 1

    entries = _list_ledger(db)
    assert len(entries) == 1
    assert entries[0]["category"] == CATEGORY_DAILY_MEALS
    assert int(entries[0]["amount_cents"]) == 2500

    queue = list_review_queue(db)
    assert len(queue) == 1
    assert queue[0]["reason"] == "unmatched"

    rule = list_classification_rules(db)[0]
    assert int(rule["hit_count"]) == 1

    audit = list_audit_events(db)
    assert any(e["event_type"] == "rule_applied" for e in audit)


def test_process_batch_observing_rule_queues_with_suggestion(db, tmp_path):
    create_classification_rule(
        db,
        match_field="item_desc",
        match_pattern="外卖",
        target_type=TYPE_CONSUMPTION,
        target_category=CATEGORY_DAILY_MEALS,
    )
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-O1,,",
        ],
    )
    processed = process_batch(db, result.batch_id)
    assert processed.posted == 0
    assert processed.queued == 1
    item = list_review_queue(db)[0]
    assert item["reason"] == "observing_rule"
    assert item["suggested_category"] == CATEGORY_DAILY_MEALS
    assert item["suggested_type"] == TYPE_CONSUMPTION
    assert _list_ledger(db) == []


def test_process_batch_promote_rule_then_auto_posts(db, tmp_path):
    create_classification_rule(
        db,
        match_field="counterparty",
        match_pattern="滴滴",
        target_type=TYPE_CONSUMPTION,
        target_category=CATEGORY_TRANSPORT,
    )
    _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 19:00:00,交通出行,滴滴出行,/,打车费,支出,26.50,余额宝,交易成功,TXN-PR1,,",
        ],
    )
    process_batch(db, 1)
    assert _list_ledger(db) == []  # 观察期不入账
    assert promote_rule(db, 1)
    _import_rows(
        db,
        tmp_path,
        [
            "2026-08-01 19:00:00,交通出行,滴滴出行,/,打车费,支出,20.00,余额宝,交易成功,TXN-PR2,,",
        ],
        name="alipay2.csv",
    )
    process_batch(db, 2)
    entries = _list_ledger(db)
    assert len(entries) == 1
    assert entries[0]["category"] == CATEGORY_TRANSPORT


def test_process_batch_transfer_neutral_posted(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 23:59:17,投资理财,余额宝,yue***@csfunds.com.cn,余额宝-单次转入,不计收支,86.73,账户余额,交易成功,TXN-T1,,",
            "2026-07-31 12:00:00,账户存取,某平台,/,不明资金流,不计收支,100.00,账户余额,交易成功,TXN-T2,,",
        ],
    )
    processed = process_batch(db, result.batch_id)
    assert processed.posted == 1
    assert processed.queued == 1
    entries = _list_ledger(db)
    assert entries[0]["entry_type"] == TYPE_TRANSFER
    queue = list_review_queue(db)
    assert queue[0]["reason"] == "other_neutral"


def test_process_batch_withdrawal_queued_high_risk(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 10:00:00,账户存取,银行卡,/,提现到银行卡,不计收支,500.00,账户余额,交易成功,TXN-W1,,",
        ],
    )
    process_batch(db, result.batch_id)
    queue = list_review_queue(db)
    assert len(queue) == 1
    assert queue[0]["reason"] == "withdrawal"
    assert int(queue[0]["priority"]) == 5
    assert _list_ledger(db) == []


def test_process_batch_person_transfer_queued(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 23:58:26,转账红包,鸿,188******65,7月份闲鱼收入,收入,27.38,账户余额,交易成功,TXN-PT1,,",
        ],
    )
    process_batch(db, result.batch_id)
    queue = list_review_queue(db)
    assert len(queue) == 1
    assert queue[0]["reason"] == "person_transfer"
    assert _list_ledger(db) == []


def test_process_batch_wechat_person_and_withdrawal(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            [datetime.datetime(2026, 7, 30, 19, 56, 10), "转账", "王琦", "转账备注:微信转账", "支出", 70, "零钱通", "对方已收钱", "WX-P1", "M1", "/"],
            [datetime.datetime(2026, 7, 21, 17, 29, 15), "零钱提现", "微信零钱", "提现", "支出", 100, "零钱", "提现已到账", "WX-W1", "M2", "/"],
        ],
        platform="wechat",
        name="wechat.xlsx",
    )
    processed = process_batch(db, result.batch_id)
    assert processed.queued == 2
    reasons = {r["reason"] for r in list_review_queue(db)}
    assert reasons == {"person_transfer", "withdrawal"}


def test_process_batch_refund_queued(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-30 17:12:03,日用百货,拼多多平台商户,pdd***@yiran.com,退款-商户单号X,不计收支,40.00,余额宝,退款成功,TXN-R1_RM001,,",
        ],
    )
    process_batch(db, result.batch_id)
    queue = list_review_queue(db)
    assert len(queue) == 1
    assert queue[0]["reason"] == "refund_pending"
    assert int(queue[0]["priority"]) == 5
    assert _list_ledger(db) == []


def test_process_batch_unmatched_income_suggests_side_income(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 12:00:00,收入,****3,155******65,闲鱼虚拟资料订单,收入,1.29,,交易成功,TXN-SI1,,",
        ],
    )
    process_batch(db, result.batch_id)
    item = list_review_queue(db)[0]
    assert item["reason"] == "unmatched"
    assert item["suggested_category"] == CATEGORY_SIDE_INCOME
    assert item["suggested_type"] == TYPE_INCOME


def test_process_batch_idempotent_rerun(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 19:21:36,日用百货,未知商户,/,消费,支出,10.00,余额宝,交易成功,TXN-ID1,,",
        ],
    )
    first = process_batch(db, result.batch_id)
    second = process_batch(db, result.batch_id)
    assert first.posted == 0 and first.queued == 1
    assert second.posted == 0 and second.queued == 0
    assert second.skipped_existing == 1
    assert len(list_review_queue(db)) == 1
    assert _list_ledger(db) == []


# ── 分组与批量确认 ──


def test_group_review_items_groups_by_counterparty(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-G1,,",
            "2026-07-30 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,25.00,余额宝,交易成功,TXN-G2,,",
            "2026-07-29 19:00:00,交通出行,滴滴出行,/,打车,支出,20.00,余额宝,交易成功,TXN-G3,,",
        ],
    )
    process_batch(db, result.batch_id)
    groups = group_review_items(db)
    by_name = {g.counterparty: g for g in groups}
    assert set(by_name) == {"美团外卖", "滴滴出行"}
    assert by_name["美团外卖"].count == 2
    assert by_name["美团外卖"].total_cents == 5500
    assert by_name["滴滴出行"].count == 1


def test_confirm_group_posts_and_creates_observing_rule(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-C1,,",
            "2026-07-30 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,25.00,余额宝,交易成功,TXN-C2,,",
        ],
    )
    process_batch(db, result.batch_id)
    confirmed = confirm_group(
        db,
        "美团外卖",
        "alipay",
        entry_type=TYPE_CONSUMPTION,
        category=CATEGORY_DAILY_MEALS,
    )
    assert confirmed.confirmed == 2
    assert confirmed.rule_id is not None
    entries = _list_ledger(db)
    assert len(entries) == 2
    assert all(e["category"] == CATEGORY_DAILY_MEALS for e in entries)
    assert list_review_queue(db) == []
    rule = list_classification_rules(db)[0]
    assert rule["status"] == "observing"
    assert rule["match_pattern"] == "美团外卖"
    audit = list_audit_events(db)
    assert any(e["event_type"] == "bulk_confirm" for e in audit)


def test_confirm_group_single_item_no_rule(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        [
            "2026-07-31 19:00:00,餐饮美食,一次商户,/,消费,支出,10.00,余额宝,交易成功,TXN-C3,,",
        ],
    )
    process_batch(db, result.batch_id)
    confirmed = confirm_group(
        db,
        "一次商户",
        "alipay",
        entry_type=TYPE_CONSUMPTION,
        category=CATEGORY_DAILY_MEALS,
    )
    assert confirmed.confirmed == 1
    assert confirmed.rule_id is None
    assert len(_list_ledger(db)) == 1


def test_confirm_group_unknown_group_raises(db, tmp_path):
    result = _import_rows(
        db,
        tmp_path,
        ["2026-07-31 19:00:00,餐饮美食,某店,/,消费,支出,10.00,余额宝,交易成功,TXN-C4,,"],
    )
    process_batch(db, result.batch_id)
    with pytest.raises(ValueError):
        confirm_group(db, "不存在商户", "alipay", entry_type=TYPE_CONSUMPTION, category="")


def test_ledger_entries_have_source_trace(db, tmp_path):
    create_classification_rule(
        db,
        match_field="counterparty",
        match_pattern="信美佳",
        target_type=TYPE_CONSUMPTION,
        target_category=CATEGORY_DAILY_MEALS,
    )
    promote_rule(db, 1)
    result = _import_rows(
        db,
        tmp_path,
        ["2026-07-31 19:21:36,日用百货,信美佳超市,/,消费,支出,25.00,余额宝,交易成功,TXN-TR1,,"],
    )
    process_batch(db, result.batch_id)
    entry = _list_ledger(db)[0]
    source = list_source_transactions(db, platform="alipay")[0]
    assert entry["source_transaction_id"] == source["id"]
    assert entry["batch_id"] == result.batch_id
