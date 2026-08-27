"""内置高置信度交通规则测试（用户指引 2026-08-27）。"""

import io

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import connect, init_db
from app.decisions.builtin_rules import apply_builtin_rules_to_pending
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


def test_builtin_transport_rules_auto_post_new_import(client):
    """新导入：地铁_/单车/骑行/公交自动入账为出行交通；地铁站店名不误伤。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-01 08:00:00,出行交通,北京地铁,/,地铁_西二旗_沙河,支出,5.00,余额宝,交易成功,TXN-BT-METRO,,",
            "2026-07-02 08:00:00,出行交通,美团,/,美团订单-美团骑行-单车-骑行费,支出,1.50,余额宝,交易成功,TXN-BT-BIKE,,",
            "2026-07-03 08:00:00,出行交通,某公交公司,/,公交-乘车,支出,1.00,余额宝,交易成功,TXN-BT-BUS,,",
            "2026-07-04 08:00:00,出行交通,某航司,/,机票订单,支出,800.00,余额宝,交易成功,TXN-BT-FLIGHT,,",
            "2026-07-05 08:00:00,出行交通,12306,/,火车票,支出,100.00,余额宝,交易成功,TXN-BT-TRAIN,,",
            "2026-07-06 08:00:00,出行交通,滴滴出行,/,特惠快车,支出,20.00,余额宝,交易成功,TXN-BT-DIDI,,",
            "2026-07-07 08:00:00,餐饮美食,美团,/,蜜雪冰城（沙河地铁站松兰路店）,支出,9.10,余额宝,交易成功,TXN-BT-TEA,,",
        ],
    )
    entries = list_ledger_entries(settings.db_path)
    assert len(entries) == 6
    assert all(e["category"] == "出行交通" for e in entries)
    pending = list_review_queue(settings.db_path)
    assert len(pending) == 1  # 蜜雪冰城仍待确认


def _enqueue_unmatched_source(settings, txn_id, direction, item_desc):
    with connect(settings.db_path) as conn:
        source_id, _ = _insert_source_transaction(
            conn,
            platform="alipay",
            source_txn_id=txn_id,
            occurred_at="2026-07-10 08:00:00",
            amount_cents=500,
            direction=direction,
            status_text="交易成功",
            counterparty="某商户",
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
        return source_id, review_id


def test_apply_builtin_rules_to_existing_pending(client):
    """既有 unmatched 待确认：只处理交通特征项，其他保留。"""
    c, settings = client
    source_id, review_id = _enqueue_unmatched_source(
        settings, "TXN-BTA-METRO", "expense", "地铁_西二旗_沙河"
    )
    _enqueue_unmatched_source(settings, "TXN-BTA-OTHER", "expense", "普通商品")

    result = apply_builtin_rules_to_pending(settings.db_path)
    assert result.posted == 1
    assert len(list_ledger_entries(settings.db_path)) == 1
    pending = list_review_queue(settings.db_path)
    assert len(pending) == 1
    assert pending[0]["source_transaction_id"] != source_id


def test_builtin_rules_do_not_touch_income_or_neutral(client):
    """内置交通规则只匹配支出方向。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-01 08:00:00,收入,某人,/,转让单车,收入,10.00,余额宝,交易成功,TXN-BT-INC,,",
            "2026-07-02 08:00:00,退款,某人,/,单车退款,不计收支,10.00,余额宝,退款成功,TXN-BT-REF,,",
        ],
    )
    assert list_ledger_entries(settings.db_path) == []
    assert len(list_review_queue(settings.db_path)) == 2
