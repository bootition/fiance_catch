"""规则筛选清扫测试（用户指引 2026-08-27）。"""

import io

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import connect, init_db
from app.decisions.confirm import confirm_review_item
from app.decisions.rule_sweep import apply_active_rules_to_pending, auto_link_unambiguous_refunds, sweep_bulk_confirm_entries
from app.ledger_repo import (
    _enqueue_review,
    _insert_source_transaction,
    list_ledger_entries,
    list_review_queue,
)
from app.settings import Settings

ALIPAY_HEADER = (
    "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,"
    "收/付款方式,交易状态,交易订单号,商家订单号,备注"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with TestClient(main.app) as c:
        yield c, settings


def _upload(client, rows):
    payload = chr(10).join(["----导出信息----", ALIPAY_HEADER] + rows).encode("gb18030")
    return client.post(
        "/imports/new",
        data={"platform": "alipay"},
        files={"file": ("sample.csv", io.BytesIO(payload), "text/csv")},
    )


def _confirm(client, counterparty, direction, entry_type, category):
    return client.post(
        "/inbox/confirm",
        data={
            "counterparty": counterparty,
            "platform": "alipay",
            "direction": direction,
            "entry_type": entry_type,
            "category": category,
        },
    )


def _enqueue_source(settings, txn_id, direction, item_desc, counterparty="某商户"):
    with connect(settings.db_path) as conn:
        source_id, _ = _insert_source_transaction(
            conn,
            platform="alipay",
            source_txn_id=txn_id,
            occurred_at="2026-07-10 08:00:00",
            amount_cents=100,
            direction=direction,
            status_text="交易成功",
            counterparty=counterparty,
            item_desc=item_desc,
            raw_type="",
            note="",
            batch_id=None,
            normalized_hash=txn_id,
        )
        review_id = _enqueue_review(
            conn, source_transaction_id=source_id, reason="unmatched", priority=1
        )
        conn.commit()
        return review_id


def test_sweep_keeps_rule_matches_and_reopens_others(client):
    """历史批量确认：命中 active 用户规则的保留，未命中的退回待确认。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-01 10:00:00,收入,闲鱼小店,/,闲鱼虚拟资料,收入,10.00,余额宝,交易成功,TXN-SW-1,,",
            "2026-07-02 10:00:00,收入,闲鱼小店,/,闲鱼虚拟资料,收入,20.00,余额宝,交易成功,TXN-SW-2,,",
            "2026-07-03 10:00:00,收入,其他小店,/,普通资料,收入,30.00,余额宝,交易成功,TXN-SW-3,,",
        ],
    )
    _confirm(c, "闲鱼小店", "income", "income", "副业收入")
    _confirm(c, "其他小店", "income", "income", "副业收入")
    assert len(list_ledger_entries(settings.db_path)) == 3

    # 用户操作 AI 显式写入规则并提升
    c.post(
        "/rules",
        data={
            "match_field": "item_desc",
            "match_pattern": "闲鱼虚拟资料",
            "platform": "alipay",
            "direction": "income",
            "target_type": "income",
            "target_category": "副业收入",
        },
    )
    assert c.post("/rules/1/promote").status_code == 200

    result = sweep_bulk_confirm_entries(settings.db_path)
    assert result.kept == 2
    assert result.reopened == 1
    assert len(list_ledger_entries(settings.db_path)) == 2
    pending = list_review_queue(settings.db_path)
    assert len(pending) == 1


def test_sweep_keeps_builtin_transport(client):
    """内置交通规则命中的历史批量确认也保留。"""
    c, settings = client
    review_id = _enqueue_source(settings, "TXN-SW-BUS", "expense", "公交-乘车")
    confirm_review_item(
        settings.db_path,
        review_id,
        entry_type="consumption",
        category="出行交通",
    )
    assert len(list_ledger_entries(settings.db_path)) == 1

    result = sweep_bulk_confirm_entries(settings.db_path)
    assert result.kept == 1
    assert result.reopened == 0
    assert len(list_ledger_entries(settings.db_path)) == 1


def test_apply_active_rules_to_pending(client):
    """AI 写入并提升规则后，可对既有 unmatched 待确认自动筛选。"""
    c, settings = client
    _enqueue_source(settings, "TXN-SW-PENDING", "income", "闲鱼虚拟资料")
    assert len(list_review_queue(settings.db_path)) == 1

    c.post(
        "/rules",
        data={
            "match_field": "item_desc",
            "match_pattern": "闲鱼虚拟资料",
            "platform": "alipay",
            "direction": "income",
            "target_type": "income",
            "target_category": "副业收入",
        },
    )
    assert c.post("/rules/1/promote").status_code == 200

    posted = apply_active_rules_to_pending(settings.db_path)
    assert posted == 1
    assert list_review_queue(settings.db_path) == []
    assert len(list_ledger_entries(settings.db_path)) == 1


def test_auto_link_unambiguous_refund(client):
    """唯一候选的退款自动冲销原消费；不再要求人工逐笔关联。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-01 10:00:00,日用百货,某店,/,商品,支出,20.00,余额宝,交易成功,TXN-REF-A,,",
            "2026-07-02 10:00:00,日用百货,某店,/,退款-商户单号X,不计收支,20.00,余额宝,退款成功,TXN-REF-A_RM1,,",
        ],
    )
    _confirm(c, "某店", "expense", "consumption", "日常娱乐")
    assert len(list_review_queue(settings.db_path)) == 1
    linked = auto_link_unambiguous_refunds(settings.db_path)
    assert linked == 1
    pending = list_review_queue(settings.db_path)
    assert all(r["reason"] != "refund_pending" for r in pending)
