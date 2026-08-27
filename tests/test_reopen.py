"""误操作退回待确认测试（用户反馈 2026-08-27）。"""

import io

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import init_db
from app.ledger_repo import list_ledger_entries, list_review_queue
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
    payload = "\n".join(["----导出信息----", ALIPAY_HEADER] + rows).encode("gb18030")
    return client.post(
        "/imports/new",
        data={"platform": "alipay"},
        files={"file": ("sample.csv", io.BytesIO(payload), "text/csv")},
    )


def test_rule_reopen_returns_group_to_inbox(client):
    """按规则批量退回：账本删除、待办恢复，误操作可以重新分类。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-01 10:00:00,收入,闲鱼小店,/,闲鱼虚拟资料,收入,10.00,余额宝,交易成功,TXN-RO-1,,",
            "2026-07-02 10:00:00,收入,闲鱼小店,/,闲鱼虚拟资料,收入,20.00,余额宝,交易成功,TXN-RO-2,,",
        ],
    )
    confirmed = c.post(
        "/inbox/confirm",
        data={
            "counterparty": "闲鱼小店",
            "platform": "alipay",
            "direction": "income",
            "entry_type": "income",
            "category": "副业收入",
        },
    )
    assert "已确认 2 项" in confirmed.text
    assert len(list_ledger_entries(settings.db_path)) == 2
    assert list_review_queue(settings.db_path) == []

    rules_page = c.get("/rules")
    assert "退回确认流水" in rules_page.text
    response = c.post("/rules/1/reopen")
    assert response.status_code == 200
    assert "已退回 2 笔" in response.text
    assert list_ledger_entries(settings.db_path) == []
    pending = list_review_queue(settings.db_path)
    assert len(pending) == 2
    assert all(r["status"] == "pending" for r in pending)


def test_single_entry_reopen_and_manual_edit_block(client):
    """单笔退回可用；已人工编辑的记录会被阻塞保留。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-01 10:00:00,收入,单笔小店,/,闲鱼虚拟资料,收入,10.00,余额宝,交易成功,TXN-RO-3,,",
        ],
    )
    c.post(
        "/inbox/confirm",
        data={
            "counterparty": "单笔小店",
            "platform": "alipay",
            "direction": "income",
            "entry_type": "income",
            "category": "副业收入",
        },
    )
    entry = list_ledger_entries(settings.db_path)[0]
    detail = c.get(f"/transactions/{entry['id']}")
    assert "退回待确认" in detail.text

    # 人工编辑后：单笔退回必须拒绝删除并明确提示
    c.post(
        f"/transactions/{entry['id']}/edit",
        data={
            "entry_type": "income",
            "amount": "12.00",
            "category": "其他收入",
            "txn_date": "2026-07-01",
            "note": "改一下",
        },
    )
    reopened = c.post(f"/transactions/{entry['id']}/reopen", follow_redirects=False)
    assert reopened.status_code == 303
    assert len(list_ledger_entries(settings.db_path)) == 1  # 仍保留

    # 未编辑的单笔可以退回
    _upload(
        c,
        ["2026-07-02 10:00:00,收入,单笔小店2,/,闲鱼虚拟资料,收入,20.00,余额宝,交易成功,TXN-RO-4,,"],
    )
    c.post(
        "/inbox/confirm",
        data={
            "counterparty": "单笔小店2",
            "platform": "alipay",
            "direction": "income",
            "entry_type": "income",
            "category": "副业收入",
        },
    )
    entry2 = next(e for e in list_ledger_entries(settings.db_path) if e['txn_date'] == '2026-07-02')
    reopened2 = c.post(f"/transactions/{entry2['id']}/reopen", follow_redirects=False)
    assert reopened2.status_code == 303
    assert len(list_ledger_entries(settings.db_path)) == 1
    assert any(
        r["status"] == "pending"
        for r in list_review_queue(settings.db_path)
    )
