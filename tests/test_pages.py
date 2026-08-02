"""阶段 5 页面级测试：概览/导入/待确认/流水/规则/批次（规格 §3）。"""

import io

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import init_db
from app.decisions.confirm import confirm_group, group_review_items, promote_rule
from app.decisions.constants import CATEGORY_DAILY_MEALS, TYPE_CONSUMPTION
from app.importing.service import import_file
from app.ledger_repo import create_classification_rule
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


def _alipay_csv_bytes(rows):
    return ("\n".join(["----导出信息----", ALIPAY_HEADER] + rows)).encode("gb18030")


def _upload(client, rows, platform="alipay"):
    return client.post(
        "/imports/new",
        data={"platform": platform},
        files={"file": ("sample.csv", io.BytesIO(_alipay_csv_bytes(rows)), "text/csv")},
    )


# ── 概览 ──


def test_overview_shows_metrics_after_confirm(client):
    c, settings = client
    rows = [
        "2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-OV-1,,",
        "2026-07-30 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,20.00,余额宝,交易成功,TXN-OV-2,,",
        "2026-07-29 12:00:00,收入,****3,155******65,闲鱼虚拟资料,收入,50.00,,交易成功,TXN-OV-3,,",
        "2026-07-28 12:00:00,转账红包,鸿,188******65,闲鱼收入,收入,27.38,账户余额,交易成功,TXN-OV-4,,",
    ]
    _upload(c, rows)
    for group in list(group_review_items(settings.db_path)):
        if group.counterparty != "美团外卖":
            continue
        confirm_group(
            settings.db_path, group.counterparty, group.platform,
            entry_type=TYPE_CONSUMPTION, category=CATEGORY_DAILY_MEALS,
        )

    response = c.get("/", params={"ym": "2026-07"})
    assert response.status_code == 200
    text = response.text
    assert "本月概览" in text
    assert "50.00" in text  # 消费合计 30+20
    assert "总消费" in text
    assert "日常消费环比" in text
    assert "消费分类排行" in text


def test_overview_shows_pending_link(client):
    c, _ = client
    _upload(c, ["2026-07-31 19:00:00,餐饮美食,某店,/,消费,支出,10.00,余额宝,交易成功,TXN-OVP-1,,"])
    response = c.get("/", params={"ym": "2026-07"})
    assert "待确认" in response.text
    assert "/inbox" in response.text


# ── 导入 ──


def test_import_upload_flow(client):
    c, _ = client
    response = _upload(
        c,
        [
            "2026-07-31 19:00:00,餐饮美食,某店,/,消费,支出,10.00,余额宝,交易成功,TXN-IMP-1,,",
            "2026-07-30 17:12:03,日用百货,拼多多平台商户,pdd***@yiran.com,退款-商户单号X,不计收支,40.00,余额宝,退款成功,TXN-IMP-2_RM1,,",
        ],
    )
    assert response.status_code == 200
    text = response.text
    assert "新增来源流水" in text
    assert "退款识别" in text
    assert "进入待确认" in text


def test_import_duplicate_dedup_shown(client):
    c, _ = client
    rows = ["2026-07-31 19:00:00,餐饮美食,某店,/,消费,支出,10.00,余额宝,交易成功,TXN-IMPD-1,,"]
    _upload(c, rows)
    response = _upload(c, rows)
    assert "重复跳过" in response.text


# ── 待确认 ──


def test_inbox_shows_high_risk_and_groups(client):
    c, _ = client
    rows = [
        "2026-07-31 10:00:00,账户存取,某银行,/,提现到银行卡,不计收支,500.00,账户余额,交易成功,TXN-IN-1,,",
        "2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-IN-2,,",
        "2026-07-30 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,20.00,余额宝,交易成功,TXN-IN-3,,",
    ]
    _upload(c, rows)
    response = c.get("/inbox")
    assert response.status_code == 200
    text = response.text
    assert "高风险区" in text
    assert "提现到银行卡" in text
    assert "美团外卖" in text  # 分类区分组
    assert "确认 2 项" in text


def test_inbox_confirm_group(client):
    c, settings = client
    rows = [
        "2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-INC-1,,",
        "2026-07-30 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,20.00,余额宝,交易成功,TXN-INC-2,,",
    ]
    _upload(c, rows)
    response = c.post(
        "/inbox/confirm",
        data={
            "counterparty": "美团外卖",
            "platform": "alipay",
            "entry_type": TYPE_CONSUMPTION,
            "category": CATEGORY_DAILY_MEALS,
        },
    )
    assert response.status_code == 200
    assert "已确认 2 项" in response.text
    assert "观察期规则" in response.text
    # 提现等高危不可批量确认
    _upload(c, ["2026-07-29 10:00:00,账户存取,某银行,/,提现,不计收支,100.00,账户余额,交易成功,TXN-INW-1,,"])
    response2 = c.post(
        "/inbox/confirm",
        data={
            "counterparty": "某银行",
            "platform": "alipay",
            "entry_type": TYPE_CONSUMPTION,
            "category": CATEGORY_DAILY_MEALS,
        },
    )
    assert "失败" in response2.text or "高风险" in response2.text


# ── 流水与补账 ──


def test_transactions_manual_entry_and_filter(client):
    c, _ = client
    response = c.post(
        "/transactions",
        data={
            "entry_type": "consumption",
            "amount": "12.50",
            "category": "日常三餐",
            "txn_date": "2026-07-15",
            "note": "手动补记",
        },
    )
    assert response.status_code == 200
    assert "已补记" in response.text
    assert "12.50" in response.text

    filtered = c.get(
        "/transactions",
        params={"category": "日常三餐", "start": "2026-07-01", "end": "2026-07-31"},
    )
    assert "12.50" in filtered.text


def test_transactions_filter_by_type(client):
    c, _ = client
    c.post(
        "/transactions",
        data={"entry_type": "income", "amount": "100.00", "category": "其他收入", "txn_date": "2026-07-15", "note": ""},
    )
    response = c.get("/transactions", params={"entry_type": "income"})
    assert "其他收入" in response.text
    expense = c.get("/transactions", params={"entry_type": "consumption"})
    assert "无匹配流水" in expense.text


def test_transactions_delete(client):
    c, settings = client
    c.post(
        "/transactions",
        data={"entry_type": "consumption", "amount": "5.00", "category": "", "txn_date": "2026-07-15", "note": "待删"},
    )
    from app.ledger_repo import list_ledger_entries

    entry_id = list_ledger_entries(settings.db_path)[0]["id"]
    response = c.post(f"/transactions/{entry_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert list_ledger_entries(settings.db_path) == []


# ── 规则 ──


def test_rules_create_promote(client):
    c, _ = client
    response = c.post(
        "/rules",
        data={
            "match_field": "counterparty",
            "match_pattern": "美团外卖",
            "target_type": TYPE_CONSUMPTION,
            "target_category": CATEGORY_DAILY_MEALS,
        },
    )
    assert response.status_code == 200
    assert "观察期规则 #1" in response.text

    response = c.post("/rules/1/promote")
    assert "已提升为自动入账" in response.text

    response = c.post("/rules/1/status/disabled")
    assert "已停用" in response.text

    response = c.post("/rules/1/status/active")
    assert "已启用" in response.text


def test_rules_rejects_blank_pattern(client):
    c, _ = client
    response = c.post(
        "/rules",
        data={"match_field": "counterparty", "match_pattern": "   ", "target_type": TYPE_CONSUMPTION, "target_category": ""},
    )
    assert "创建失败" in response.text


# ── 批次 ──


def test_batches_list_and_revoke(client):
    c, settings = client
    rows = ["2026-07-31 19:00:00,餐饮美食,某店,/,消费,支出,10.00,余额宝,交易成功,TXN-BT-1,,"]
    from pathlib import Path

    path = settings.data_dir / "x.csv"
    path.write_bytes(_alipay_csv_bytes(rows))
    import_file(settings.db_path, path, "alipay")

    response = c.get("/imports")
    assert response.status_code == 200
    assert "批次列表" in response.text
    assert "x.csv" in response.text

    response = c.post("/imports/1/revoke")
    assert "已撤销批次" in response.text
    assert "已撤销" in response.text
