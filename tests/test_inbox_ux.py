"""Inbox UX 优化测试：HTMX 局部刷新片段、退款候选刷新路由、无候选指引（2026-08-27）。"""

import io

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import init_db
from app.decisions.constants import CATEGORY_DAILY_MEALS, TYPE_CONSUMPTION
from app.importing.service import import_file
from app.ledger_repo import list_review_queue
from app.refunds.matching import find_refund_candidates
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


def _review_id_by_txn(settings, txn_id):
    for r in list_review_queue(settings.db_path):
        source = _source_by_txn(settings, txn_id)
        if r["source_transaction_id"] == source["id"] and r["status"] == "pending":
            return r["id"]
    return None


def _source_by_txn(settings, txn_id):
    with __import__("sqlite3").connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT id FROM source_transactions WHERE source_txn_id = ?", (txn_id,)
        ).fetchone()
    return {"id": row[0]}


def test_inbox_confirm_returns_category_section_fragment(client):
    """批量确认后返回分类区局部片段（非整页），带 flash 与 OOB 计数。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-UX-1,,",
            "2026-07-30 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,20.00,余额宝,交易成功,TXN-UX-2,,",
        ],
    )
    response = c.post(
        "/inbox/confirm",
        data={
            "counterparty": "美团外卖",
            "platform": "alipay",
            "direction": "expense",
            "entry_type": TYPE_CONSUMPTION,
            "category": CATEGORY_DAILY_MEALS,
        },
    )
    assert response.status_code == 200
    text = response.text
    assert "<html" not in text  # 局部片段而非整页
    assert 'id="category-section"' in text
    assert "已确认 2 项" in text
    assert 'hx-swap-oob="innerHTML"' in text  # 导航计数 OOB


def test_inbox_resolve_returns_high_risk_section_fragment(client):
    """逐笔定性后返回高风险区局部片段（非整页），带 flash。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-31 10:00:00,账户存取,某银行,/,提现到银行卡,不计收支,500.00,账户余额,交易成功,TXN-UXW-1,,",
        ],
    )
    inbox = c.get("/inbox")
    assert "提现到银行卡" in inbox.text
    review_id = _review_id_by_txn(settings, "TXN-UXW-1")
    response = c.post(
        "/inbox/resolve",
        data={"review_id": review_id, "purpose": "transfer", "category": ""},
    )
    assert response.status_code == 200
    text = response.text
    assert "<html" not in text
    assert 'id="high-risk-section"' in text
    assert "已定性" in text


def test_inbox_refund_no_candidate_hint_and_refresh(client):
    """退款在原消费未确认时无候选：页面给指引与刷新按钮；确认消费后刷新出现候选；关联后刷新返回空。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,20.00,余额宝,交易成功,TXN-UX-ABC,,",
            "2026-07-11 10:00:00,日用百货,某店,/,退款-消费,不计收支,20.00,余额宝,退款成功,TXN-UX-ABC_RM999,,",
        ],
    )
    # 原消费未确认 → 无候选 → 页面给出指引与刷新按钮
    inbox = c.get("/inbox")
    assert "退款待办" in inbox.text
    assert "刷新候选" in inbox.text
    assert "常见原因" in inbox.text
    assert "先到下方分类区" in inbox.text

    review_id = _review_id_by_txn(settings, "TXN-UX-ABC_RM999")
    assert review_id is not None
    frag = c.get(f"/inbox/refund-candidates/{review_id}")
    assert frag.status_code == 200
    assert "未找到候选原消费" in frag.text

    # 确认原消费（分类区批量确认）
    resp = c.post(
        "/inbox/confirm",
        data={
            "counterparty": "某店",
            "platform": "alipay",
            "direction": "expense",
            "entry_type": TYPE_CONSUMPTION,
            "category": CATEGORY_DAILY_MEALS,
        },
    )
    assert "已确认 1 项" in resp.text

    # 刷新候选 → 出现原交易订单号匹配
    frag = c.get(f"/inbox/refund-candidates/{review_id}")
    assert frag.status_code == 200
    assert "原交易订单号匹配" in frag.text

    # 关联退款 → 待办关闭 → 再刷新返回空片段（卡片被移除）
    refund_source_id = _source_by_txn(settings, "TXN-UX-ABC_RM999")["id"]
    candidates = find_refund_candidates(settings.db_path, refund_source_id)
    assert candidates and candidates[0].match_reason == "原交易订单号匹配"
    link = c.post(
        "/inbox/refund/link",
        data={
            "review_id": review_id,
            "refund_source_id": refund_source_id,
            "original_ledger_id": candidates[0].ledger_id,
        },
    )
    assert "已关联退款" in link.text
    frag = c.get(f"/inbox/refund-candidates/{review_id}")
    assert frag.status_code == 200
    assert frag.text == ""  # 待办已处理，卡片移除
