"""阶段 5 红队审查回归测试（docs/reports/12_phase5_red_team_review_2026-08-01.md）。"""

import io

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import init_db
from app.decisions.confirm import confirm_group, group_review_items, promote_rule
from app.decisions.constants import CATEGORY_DAILY_MEALS, TYPE_CONSUMPTION
from app.importing.service import import_file
from app.ledger_repo import (
    create_classification_rule,
    get_ledger_entry,
    list_classification_rules,
    list_ledger_entries,
    update_ledger_entry,
)
from app.refunds.linking import link_refund_to_ledger
from app.settings import Settings
from app.stats import overview_stats

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
    return client.post(
        "/imports/new",
        data={"platform": "alipay"},
        files={"file": ("s.csv", io.BytesIO(("\n".join(["----导出----", ALIPAY_HEADER] + rows)).encode("gb18030")), "text/csv")},
    )


def _confirm_group(client, settings, counterparty, category=CATEGORY_DAILY_MEALS):
    for g in group_review_items(settings.db_path):
        if g.counterparty == counterparty:
            confirm_group(
                settings.db_path, g.counterparty, g.platform,
                entry_type=TYPE_CONSUMPTION, category=category,
            )
            return
    raise AssertionError(f"group not found: {counterparty}")


# ── P1-1：规则观察期不可经状态路由直跳 active ──


def test_observing_rule_cannot_jump_to_active_via_status_route(client):
    c, settings = client
    c.post(
        "/rules",
        data={"match_field": "counterparty", "match_pattern": "美团外卖", "target_type": TYPE_CONSUMPTION, "target_category": CATEGORY_DAILY_MEALS},
    )
    assert list_classification_rules(settings.db_path)[0]["status"] == "observing"

    # 红队复现：POST /rules/1/status/active → 必须拒绝（仍为 observing）
    response = c.post("/rules/1/status/active")
    assert response.status_code == 200
    assert "操作失败" in response.text
    assert list_classification_rules(settings.db_path)[0]["status"] == "observing"

    # 服务层同样拒绝
    from app.ledger_repo import update_rule_status

    assert update_rule_status(settings.db_path, 1, "active") is False

    # promote 后 active 生效；停用后重新启用允许
    assert promote_rule(settings.db_path, 1)
    assert list_classification_rules(settings.db_path)[0]["status"] == "active"
    assert update_rule_status(settings.db_path, 1, "disabled")
    assert update_rule_status(settings.db_path, 1, "active")


def test_observing_rule_not_auto_posting_after_rejected_jump(client):
    """直跳被拒后，规则仍按观察期语义（命中只预填待确认）。"""
    c, settings = client
    c.post(
        "/rules",
        data={"match_field": "counterparty", "match_pattern": "美团外卖", "target_type": TYPE_CONSUMPTION, "target_category": CATEGORY_DAILY_MEALS},
    )
    c.post("/rules/1/status/active")  # 被拒
    _upload(c, ["2026-07-31 19:00:00,餐饮美食,美团外卖,/,外卖订单,支出,30.00,余额宝,交易成功,TXN-RJ-1,,"])
    from app.ledger_repo import list_review_queue

    queue = list_review_queue(settings.db_path)
    assert len(queue) == 1
    assert queue[0]["reason"] == "observing_rule"
    assert list_ledger_entries(settings.db_path) == []


# ── P1-2：首页统计对多笔部分退款正确聚合 ──


def _setup_partial_refunded_consumption(client, settings):
    """¥50 消费 + ¥20、¥30 两笔部分退款 → 净额应为 0。"""
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,50.00,余额宝,交易成功,TXN-PR-1,,",
        "2026-07-11 10:00:00,日用百货,某店,/,退款-消费,不计收支,20.00,余额宝,退款成功,TXN-PR-1_RM1,,",
        "2026-07-12 10:00:00,日用百货,某店,/,退款-消费,不计收支,30.00,余额宝,退款成功,TXN-PR-1_RM2,,",
        "2026-07-13 10:00:00,日用百货,另一店,/,另一消费,支出,10.00,余额宝,交易成功,TXN-PR-2,,",
    ]
    _upload(client, rows)
    _confirm_group(client, settings, "某店")
    _confirm_group(client, settings, "另一店")
    ledger = {e["source_transaction_id"]: e["id"] for e in list_ledger_entries(settings.db_path)}
    sources = {}
    from app.ledger_repo import list_source_transactions

    for s in list_source_transactions(settings.db_path):
        sources[s["source_txn_id"]] = s["id"]
    link_refund_to_ledger(settings.db_path, sources["TXN-PR-1_RM1"], ledger[sources["TXN-PR-1"]])
    link_refund_to_ledger(settings.db_path, sources["TXN-PR-1_RM2"], ledger[sources["TXN-PR-1"]])


def test_overview_partial_refunds_not_double_counted(client):
    """红队复现：¥50 消费 + ¥20/¥30 退款 → 净消费 ¥0 + 另一笔 ¥10。"""
    c, settings = client
    _setup_partial_refunded_consumption(c, settings)
    stats = overview_stats(settings.db_path, "2026-07")
    assert stats["total_consumption_cents"] == 1000  # 0 + 10


def test_overview_partial_refunds_category_ranking(client):
    c, settings = client
    _setup_partial_refunded_consumption(c, settings)
    stats = overview_stats(settings.db_path, "2026-07")
    ranking = dict(stats["ranking"])
    assert ranking.get("日常三餐", 0) == 1000


def test_overview_cross_period_refund_single_month(client):
    """跨期退款：6 月消费 7 月退款，7 月不显示负额。"""
    c, settings = client
    rows = [
        "2026-06-20 10:00:00,日用百货,某店,/,消费,支出,50.00,余额宝,交易成功,TXN-CP-1,,",
        "2026-07-05 10:00:00,日用百货,某店,/,退款-消费,不计收支,50.00,余额宝,退款成功,TXN-CP-1_RM1,,",
    ]
    _upload(c, rows)
    _confirm_group(c, settings, "某店")
    sources = {}
    from app.ledger_repo import list_source_transactions

    for s in list_source_transactions(settings.db_path):
        sources[s["source_txn_id"]] = s["id"]
    ledger = {e["source_transaction_id"]: e["id"] for e in list_ledger_entries(settings.db_path)}
    link_refund_to_ledger(settings.db_path, sources["TXN-CP-1_RM1"], ledger[sources["TXN-CP-1"]])

    june = overview_stats(settings.db_path, "2026-06")
    july = overview_stats(settings.db_path, "2026-07")
    assert june["total_consumption_cents"] == 0  # 回写原周期
    assert july["total_consumption_cents"] == 0  # 7 月无消费、不显示负额


# ── P1-3：已退款消费不可编辑为负净额 ──


def test_edit_refunded_consumption_below_refund_rejected(client):
    c, settings = client
    _setup_partial_refunded_consumption(c, settings)
    ledger = list_ledger_entries(settings.db_path)
    refunded_entry = next(e for e in ledger if e["amount_cents"] == 5000)

    with pytest.raises(ValueError, match="below linked refund"):
        update_ledger_entry(
            settings.db_path,
            refunded_entry["id"],
            entry_type=TYPE_CONSUMPTION,
            amount_cents=1000,  # 改小 → 负净额 → 拒绝
            category=CATEGORY_DAILY_MEALS,
            txn_date="2026-07-10",
            note="",
        )
    assert int(get_ledger_entry(settings.db_path, refunded_entry["id"])["amount_cents"]) == 5000


def test_edit_refunded_consumption_type_change_rejected(client):
    c, settings = client
    _setup_partial_refunded_consumption(c, settings)
    ledger = list_ledger_entries(settings.db_path)
    refunded_entry = next(e for e in ledger if e["amount_cents"] == 5000)

    with pytest.raises(ValueError, match="cannot change entry type"):
        update_ledger_entry(
            settings.db_path,
            refunded_entry["id"],
            entry_type="income",
            amount_cents=5000,
            category="",
            txn_date="2026-07-10",
            note="",
        )


def test_edit_non_refunded_consumption_allowed(client):
    c, settings = client
    _setup_partial_refunded_consumption(c, settings)
    ledger = list_ledger_entries(settings.db_path)
    normal = next(e for e in ledger if e["amount_cents"] == 1000)

    assert update_ledger_entry(
        settings.db_path,
        normal["id"],
        entry_type=TYPE_CONSUMPTION,
        amount_cents=800,
        category=CATEGORY_DAILY_MEALS,
        txn_date="2026-07-13",
        note="改备注",
    )


def test_edit_page_rejects_refunded_lower_amount(client):
    """页面路由层面：改小已退款消费金额 → 失败提示，金额不变。"""
    c, settings = client
    _setup_partial_refunded_consumption(c, settings)
    ledger = list_ledger_entries(settings.db_path)
    refunded_entry = next(e for e in ledger if e["amount_cents"] == 5000)

    response = c.post(
        f"/transactions/{refunded_entry['id']}/edit",
        data={
            "entry_type": TYPE_CONSUMPTION,
            "amount": "10.00",
            "category": CATEGORY_DAILY_MEALS,
            "txn_date": "2026-07-10",
            "note": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    stats = overview_stats(settings.db_path, "2026-07")
    assert stats["total_consumption_cents"] == 1000  # 净额未被破坏


# ── P1：删除已关联退款的消费不返回 500 ──


def test_delete_refund_linked_entry_rejected_at_repo(client):
    """仓储层：已关联退款的记录删除 → ValueError（非 500 的 IntegrityError）。"""
    c, settings = client
    _setup_partial_refunded_consumption(c, settings)
    ledger = list_ledger_entries(settings.db_path)
    refunded_entry = next(e for e in ledger if e["amount_cents"] == 5000)

    from app.ledger_repo import delete_ledger_entry

    with pytest.raises(ValueError, match="linked refunds"):
        delete_ledger_entry(settings.db_path, refunded_entry["id"])
    # 记录与退款链接均保留
    assert get_ledger_entry(settings.db_path, refunded_entry["id"]) is not None
    from app.ledger_repo import list_refund_links

    assert len(list_refund_links(settings.db_path, original_ledger_id=refunded_entry["id"])) == 2


def test_delete_refund_linked_entry_page_no_500(client):
    """路由层：删除已关联退款消费 → 303 + 提示，不返回 500，统计不破坏。"""
    c, settings = client
    _setup_partial_refunded_consumption(c, settings)
    ledger = list_ledger_entries(settings.db_path)
    refunded_entry = next(e for e in ledger if e["amount_cents"] == 5000)

    response = c.post(
        f"/transactions/{refunded_entry['id']}/delete",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "flash=" in response.headers["location"]
    # 跟随重定向：页面显示业务提示而非 500
    followed = c.get(response.headers["location"].split("?")[1] and f"/transactions?{response.headers['location'].split('?')[1]}")
    assert followed.status_code == 200
    assert "删除失败" in followed.text
    assert "linked refunds" in followed.text or "关联退款" in followed.text
    # 记录仍在、退款链接仍在、统计未被破坏
    assert get_ledger_entry(settings.db_path, refunded_entry["id"]) is not None
    stats = overview_stats(settings.db_path, "2026-07")
    assert stats["total_consumption_cents"] == 1000
