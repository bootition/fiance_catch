"""阶段 3 红队审查回归测试（docs/reports/05_phase3_red_team_review_2026-08-01.md）。"""

import datetime

import pytest

from app.db import init_db
from app.decisions.confirm import confirm_group, group_review_items, promote_rule
from app.decisions.constants import TYPE_CONSUMPTION
from app.decisions.engine import process_batch
from app.importing.service import import_file
from app.ledger_repo import (
    create_classification_rule,
    get_import_batch,
    list_ledger_entries,
    list_review_queue,
)
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


# ── P1：高风险原因禁止批量确认 ──


@pytest.mark.parametrize(
    "row,counterparty,expected_reason",
    [
        (
            "2026-07-31 10:00:00,账户存取,某银行,/,提现到银行卡,不计收支,500.00,账户余额,交易成功,TXN-HW,,",
            "某银行",
            "withdrawal",
        ),
        (
            "2026-07-31 23:58:26,转账红包,鸿,188******65,7月份闲鱼收入,收入,27.38,账户余额,交易成功,TXN-HP,,",
            "鸿",
            "person_transfer",
        ),
        (
            "2026-07-30 17:12:03,日用百货,拼多多平台商户,pdd***@yiran.com,退款-商户单号X,不计收支,40.00,余额宝,退款成功,TXN-HR_RM001,,",
            "拼多多平台商户",
            "refund_pending",
        ),
        (
            "2026-07-31 12:00:00,账户存取,某平台,/,不明资金流,不计收支,100.00,账户余额,交易成功,TXN-HN,,",
            "某平台",
            "other_neutral",
        ),
    ],
)
def test_high_risk_reasons_cannot_be_bulk_confirmed(
    db, tmp_path, row, counterparty, expected_reason
):
    result = _import(db, tmp_path, [row])
    process_batch(db, result.batch_id)
    assert list_review_queue(db)[0]["reason"] == expected_reason

    # 分组视图不含高风险项
    assert group_review_items(db) == []

    # 即使构造同名商户组，confirm_group 也必须拒绝
    with pytest.raises(ValueError):
        confirm_group(
            db,
            counterparty,
            "alipay",
            entry_type=TYPE_CONSUMPTION,
            category="x",
        )
    # 拒绝后不得产生账本记录、不得关闭待确认
    assert list_ledger_entries(db) == []
    assert len(list_review_queue(db)) == 1


def test_mixed_group_with_high_risk_rejected(db, tmp_path):
    """同商户组内混入高风险项：分组视图只含分类区项，高风险项不参与批量确认。"""
    rows = [
        "2026-07-31 19:00:00,餐饮美食,某商户,/,消费,支出,30.00,余额宝,交易成功,TXN-MIX-1,,",
        "2026-07-31 10:00:00,账户存取,某商户,/,提现到银行卡,不计收支,500.00,账户余额,交易成功,TXN-MIX-2,,",
    ]
    result = _import(db, tmp_path, rows)
    process_batch(db, result.batch_id)

    groups = group_review_items(db)
    assert len(groups) == 1
    assert groups[0].counterparty == "某商户"
    assert groups[0].count == 1  # 仅普通分类项；提现项被隔离
    assert groups[0].items[0].reason == "unmatched"

    confirmed = confirm_group(
        db,
        "某商户",
        "alipay",
        entry_type=TYPE_CONSUMPTION,
        category="日常三餐",
    )
    assert confirmed.confirmed == 1
    entries = list_ledger_entries(db)
    assert len(entries) == 1  # 只有普通项入账
    assert int(entries[0]["amount_cents"]) == 3000
    reasons = {r["reason"] for r in list_review_queue(db)}
    assert reasons == {"withdrawal"}  # 提现项仍待确认


# ── P1：空规则模式 ──


def test_create_rule_rejects_blank_pattern(db):
    with pytest.raises(ValueError):
        create_classification_rule(
            db,
            match_field="counterparty",
            match_pattern="   ",
            target_type=TYPE_CONSUMPTION,
            target_category="x",
        )
    with pytest.raises(ValueError):
        create_classification_rule(
            db,
            match_field="item_desc",
            match_pattern="",
            target_type=TYPE_CONSUMPTION,
            target_category="x",
        )


def test_blank_pattern_rule_rejected_by_schema(db):
    """schema CHECK(TRIM(match_pattern) <> '') 直接拒绝空模式规则（含手工注入）。"""
    import sqlite3

    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO classification_rules(match_field, match_pattern, target_type, target_category)
                VALUES ('counterparty', '', 'consumption', 'bad')
                """
            )


def test_blank_pattern_rule_never_matches(db, tmp_path):
    """匹配函数防御：空/空白模式绝不命中任何交易（红队 P1 复现路径）。"""
    from app.decisions.rules import _text_contains

    assert _text_contains("无关商户消费", "") is False
    assert _text_contains("无关商户消费", "   ") is False

    # 端到端：无规则时无关商户消费进入待确认而非自动入账
    result = _import(
        db,
        tmp_path,
        ["2026-07-31 19:00:00,日用百货,无关商户,/,消费,支出,10.00,余额宝,交易成功,TXN-BLANK-1,,"],
    )
    processed = process_batch(db, result.batch_id)
    assert processed.posted == 0
    assert processed.queued == 1
    assert list_ledger_entries(db) == []
    assert list_review_queue(db)[0]["reason"] == "unmatched"


# ── P2：幂等重跑 pending_count 与真实队列一致 ──


def _withdrawal_rows(prefix="TXN-W"):
    return [
        f"2026-07-31 10:00:00,账户存取,某银行,/,提现,不计收支,500.00,账户余额,交易成功,{prefix}-1,,",
        f"2026-07-31 11:00:00,账户存取,某银行,/,提现,不计收支,200.00,账户余额,交易成功,{prefix}-2,,",
    ]


def test_rerun_keeps_real_pending_count(db, tmp_path):
    result = _import(db, tmp_path, _withdrawal_rows())
    process_batch(db, result.batch_id)
    assert int(get_import_batch(db, result.batch_id)["pending_count"]) == 2
    rerun = process_batch(db, result.batch_id)
    assert rerun.skipped_existing == 2
    assert int(get_import_batch(db, result.batch_id)["pending_count"]) == 2
    assert len(list_review_queue(db)) == 2


def test_partial_confirm_rerun_pending_count(db, tmp_path):
    result = _import(db, tmp_path, _withdrawal_rows(prefix="TXN-PC"))
    process_batch(db, result.batch_id)
    # 手工关闭一条（模拟未来阶段 4 的受约束流程）
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE review_queue SET status='dismissed', resolved_at=datetime('now') WHERE id = (SELECT MIN(id) FROM review_queue)"
        )
    process_batch(db, result.batch_id)
    assert int(get_import_batch(db, result.batch_id)["pending_count"]) == 1
    assert len(list_review_queue(db)) == 1  # pending
    assert len(list_review_queue(db, status="dismissed")) == 1


def test_rerun_mixed_reasons_pending_count(db, tmp_path):
    rows = _withdrawal_rows(prefix="TXN-MX") + [
        "2026-07-31 19:00:00,餐饮美食,未知商户,/,消费,支出,30.00,余额宝,交易成功,TXN-MX-3,,",
        "2026-07-30 17:12:03,日用百货,拼多多平台商户,pdd***@yiran.com,退款-商户单号X,不计收支,40.00,余额宝,退款成功,TXN-MX-4_RM001,,",
    ]
    result = _import(db, tmp_path, rows)
    process_batch(db, result.batch_id)
    assert int(get_import_batch(db, result.batch_id)["pending_count"]) == 4
    process_batch(db, result.batch_id)
    assert int(get_import_batch(db, result.batch_id)["pending_count"]) == 4
    assert len(list_review_queue(db)) == 4


# ── P2：批量确认同步批次 pending_count ──


def test_confirm_group_syncs_batch_pending_count(db, tmp_path):
    rows = [
        "2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-CP-1,,",
        "2026-07-30 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,25.00,余额宝,交易成功,TXN-CP-2,,",
    ]
    result = _import(db, tmp_path, rows)
    process_batch(db, result.batch_id)
    assert int(get_import_batch(db, result.batch_id)["pending_count"]) == 2
    confirm_group(
        db,
        "美团外卖",
        "alipay",
        entry_type=TYPE_CONSUMPTION,
        category="日常三餐",
    )
    assert int(get_import_batch(db, result.batch_id)["pending_count"]) == 0


def test_confirm_group_cross_batch_syncs_all_batches(db, tmp_path):
    """同一商户分组跨两个批次：确认后两个批次的 pending_count 都同步。"""
    r1 = _import(
        db,
        tmp_path,
        ["2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-CX-1,,"],
        name="a.csv",
    )
    r2 = _import(
        db,
        tmp_path,
        ["2026-07-30 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,25.00,余额宝,交易成功,TXN-CX-2,,"],
        name="b.csv",
    )
    process_batch(db, r1.batch_id)
    process_batch(db, r2.batch_id)
    assert int(get_import_batch(db, r1.batch_id)["pending_count"]) == 1
    assert int(get_import_batch(db, r2.batch_id)["pending_count"]) == 1

    groups = group_review_items(db)
    meituan = next(g for g in groups if g.counterparty == "美团外卖")
    assert meituan.count == 2

    confirm_group(
        db,
        "美团外卖",
        "alipay",
        entry_type=TYPE_CONSUMPTION,
        category="日常三餐",
    )
    assert int(get_import_batch(db, r1.batch_id)["pending_count"]) == 0
    assert int(get_import_batch(db, r2.batch_id)["pending_count"]) == 0
    entries = list_ledger_entries(db)
    assert len(entries) == 2
