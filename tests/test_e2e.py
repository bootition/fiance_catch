"""阶段 6 端到端测试（规格 §7.6/§8 验收条件）。

使用匿名化固定样本（tests/samples/，真实导出格式）走完整用户流程：
导入 → 决策 → 批量确认 → 规则 → 退款关联 → 统计 → 撤销。
"""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import init_db
from app.decisions.confirm import confirm_group, group_review_items, promote_rule
from app.decisions.constants import (
    CATEGORY_DAILY_MEALS,
    CATEGORY_TRANSPORT,
    TYPE_CONSUMPTION,
)
from app.importing.service import import_file
from app.ledger_repo import (
    create_classification_rule,
    get_ledger_entry,
    list_ledger_entries,
    list_review_queue,
    list_source_transactions,
    update_ledger_entry,
)
from app.refunds.linking import link_refund_to_ledger
from app.refunds.matching import find_refund_candidates
from app.revoke import revoke_batch
from app.settings import Settings
from app.stats import overview_stats

SAMPLES = Path(__file__).resolve().parent / "samples"
ALIPAY_SAMPLE = SAMPLES / "alipay_sample.csv"
WECHAT_SAMPLE = SAMPLES / "wechat_sample.xlsx"


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with TestClient(main.app) as c:
        yield c, settings


def _upload(client, path, platform):
    return client.post(
        "/imports/new",
        data={"platform": platform},
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )


def _confirm_groups(settings, only=None):
    """批量确认分类区（可选限定商户）；返回确认的组数。"""
    count = 0
    for group in group_review_items(settings.db_path):
        if only and group.counterparty != only:
            continue
        item = group.items[0]
        entry_type = item.suggested_type or TYPE_CONSUMPTION
        category = item.suggested_category or CATEGORY_DAILY_MEALS
        confirm_group(
            settings.db_path,
            group.counterparty,
            group.platform,
            entry_type=entry_type,
            category=category,
        )
        count += 1
    return count


def _source_ids(settings):
    return {s["source_txn_id"]: s["id"] for s in list_source_transactions(settings.db_path)}


def _ledger_by_source(settings):
    """{来源交易单号: 账本记录 id}。"""
    sources = _source_ids(settings)
    by_source_id = {
        e["source_transaction_id"]: e["id"]
        for e in list_ledger_entries(settings.db_path)
        if e["source_transaction_id"] is not None
    }
    return {
        txn_id: entry_id
        for txn_id, source_id in sources.items()
        if source_id in by_source_id
        for entry_id in [by_source_id[source_id]]
    }


# ── 验收：可导入真实格式且不保存上传原文件 ──


def test_e2e_import_real_formats_no_original_saved(client):
    c, settings = client
    response = _upload(c, ALIPAY_SAMPLE, "alipay")
    assert response.status_code == 200
    assert "导入结果" in response.text
    response2 = _upload(c, WECHAT_SAMPLE, "wechat")
    assert response2.status_code == 200

    # 原始文件不落库：data_dir 无样本文件
    leftover = [p.name for p in settings.data_dir.iterdir() if p.name != "t.sqlite"]
    assert leftover == []


# ── 验收：重复导入不产生重复流水并展示跳过数量 ──


def test_e2e_duplicate_import_no_duplicates(client):
    c, settings = client
    first = _upload(c, ALIPAY_SAMPLE, "alipay")
    assert "新增来源流水" in first.text
    second = _upload(c, ALIPAY_SAMPLE, "alipay")
    assert second.status_code == 200
    assert "重复跳过" in second.text

    sources = list_source_transactions(settings.db_path, platform="alipay")
    txn_ids = [s["source_txn_id"] for s in sources]
    assert len(txn_ids) == len(set(txn_ids))
    # 11 行解析 - 1 关闭 = 10 入库；重复导入新增 0
    assert len(txn_ids) == 10


# ── 验收：可信规则自动入账、观察期不自动入账、异常入队 ──


def test_e2e_rule_active_auto_posts_and_observing_queues(client):
    c, settings = client
    # 滴滴 → active 规则
    create_classification_rule(
        settings.db_path,
        match_field="counterparty",
        match_pattern="滴滴出行",
        target_type=TYPE_CONSUMPTION,
        target_category=CATEGORY_TRANSPORT,
    )
    promote_rule(settings.db_path, 1)
    # 食堂 → 观察期规则（预填不自动入账）
    create_classification_rule(
        settings.db_path,
        match_field="counterparty",
        match_pattern="学校食堂",
        target_type=TYPE_CONSUMPTION,
        target_category=CATEGORY_DAILY_MEALS,
    )

    _upload(c, ALIPAY_SAMPLE, "alipay")

    # 滴滴自动入账（transport）
    entries = list_ledger_entries(settings.db_path)
    transport = [e for e in entries if e["category"] == CATEGORY_TRANSPORT]
    assert len(transport) == 1
    assert int(transport[0]["amount_cents"]) == 2000

    # 食堂观察期：预填待确认，不自动入账
    meals_queued = [r for r in list_review_queue(settings.db_path) if r["reason"] == "observing_rule"]
    assert len(meals_queued) == 2
    assert meals_queued[0]["suggested_category"] == CATEGORY_DAILY_MEALS
    assert not [e for e in entries if e["category"] == CATEGORY_DAILY_MEALS]


# ── 验收：提现/人际/退款未经确认不污染统计 ──


def test_e2e_high_risk_not_polluting_stats(client):
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")

    reasons = {r["reason"] for r in list_review_queue(settings.db_path)}
    assert {"withdrawal", "person_transfer", "refund_pending"} <= reasons

    # 未确认前：消费统计只含自动入账（无规则 → 0）；调拨不计消费
    stats = overview_stats(settings.db_path, "2026-07")
    assert stats["total_consumption_cents"] == 0
    assert stats["total_income_cents"] == 0
    assert stats["pending_count"] == 9  # 食堂观察2 + 滴滴/拼多多消费/闲鱼 unmatched 3 + 提现/人际 2 + 退款 2


# ── 验收：跨期退款关联后原周期统计反映实际净成本 ──


def test_e2e_cross_period_refund_net_cost(client):
    c, settings = client
    # 6 月消费 + 7 月退款（同批次同商户单号匹配）
    rows = [
        "2026-06-20 10:00:00,日用百货,某店,/,消费,支出,50.00,余额宝,交易成功,TXN-E2E-JUN1,,",
        "2026-07-05 10:00:00,日用百货,某店,/,退款-消费,不计收支,50.00,余额宝,退款成功,TXN-E2E-JUN1_RM1,,",
    ]
    path = settings.data_dir / "cross.csv"
    path.write_bytes(
        ("\n".join(["----导出----", "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注"] + rows)).encode("gb18030")
    )
    import_file(settings.db_path, path, "alipay")
    from app.decisions.engine import process_batch

    batches = [b for b in __import__("app.ledger_repo", fromlist=["list_import_batches"]).list_import_batches(settings.db_path)]
    process_batch(settings.db_path, batches[0]["id"])
    _confirm_groups(settings)

    sources = _source_ids(settings)
    ledger = _ledger_by_source(settings)
    link_refund_to_ledger(settings.db_path, sources["TXN-E2E-JUN1_RM1"], ledger["TXN-E2E-JUN1"])

    june = overview_stats(settings.db_path, "2026-06")
    assert june["total_consumption_cents"] == 0  # 50 - 50 回写原周期
    july = overview_stats(settings.db_path, "2026-07")
    assert july["total_consumption_cents"] == 0  # 7 月无净消费


# ── 验收：同商户批量确认并创建观察规则 ──


def test_e2e_bulk_confirm_creates_observing_rule(client):
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")
    confirmed = _confirm_groups(settings, only="学校食堂")
    assert confirmed == 1
    assert len([e for e in list_ledger_entries(settings.db_path) if e["category"] == CATEGORY_DAILY_MEALS]) == 2

    from app.ledger_repo import list_classification_rules

    rules = list_classification_rules(settings.db_path)
    assert any(r["match_pattern"] == "学校食堂" and r["status"] == "observing" for r in rules)


# ── 验收：安全撤销（编辑/关联阻塞） ──


def test_e2e_revoke_blocks_edited_and_linked(client):
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")
    _confirm_groups(settings)

    sources = _source_ids(settings)
    ledger = _ledger_by_source(settings)

    # 拼多多消费 → 关联退款（退款单号 _RM 前缀 = 原订单号 → 100 分）
    candidates = find_refund_candidates(settings.db_path, sources["AP-20260709-001_RM001"])
    assert candidates and candidates[0].match_reason == "原交易订单号匹配"
    link_refund_to_ledger(settings.db_path, sources["AP-20260709-001_RM001"], ledger["AP-20260709-001"])

    # 手动编辑一笔（学校食堂已确认消费，未关联退款）
    from app.ledger_repo import list_import_batches

    batch_id = list_import_batches(settings.db_path)[0]["id"]
    auto = next(e for e in list_ledger_entries(settings.db_path) if int(e["amount_cents"]) == 1550)
    update_ledger_entry(
        settings.db_path,
        auto["id"],
        entry_type=TYPE_CONSUMPTION,
        amount_cents=1550,
        category="改后分类",
        txn_date="2026-07-01",
        note="人工改",
    )

    result = revoke_batch(settings.db_path, batch_id)
    blocked_reasons = {b.reason for b in result.blocked}
    assert "manual_edited" in blocked_reasons
    assert "refund_linked" in blocked_reasons
    # 拼多多消费与退款来源、被编辑记录保留
    remaining = list_source_transactions(settings.db_path)
    remaining_ids = {s["source_txn_id"] for s in remaining}
    assert {"AP-20260709-001", "AP-20260709-001_RM001"} <= remaining_ids
    assert get_ledger_entry(settings.db_path, auto["id"]) is not None
    # 未被编辑/关联的已删
    assert "AP-20260706-001" not in remaining_ids  # 余额宝调拨来源已删


# ── 验收：首页指标与日常环比口径 ──


def test_e2e_overview_daily_ring_fence(client):
    c, settings = client
    # 制造旅游 + 副业 + 收入，确认日常五类环比不受影响
    rows = [
        "2026-06-20 10:00:00,餐饮美食,六月食堂,/,午餐,支出,10.00,余额宝,交易成功,TXN-OV-JUN1,,",
        "2026-07-10 10:00:00,酒店旅游,某航司,/,机票,支出,800.00,余额宝,交易成功,TXN-OV-JUL1,,",
        "2026-07-11 10:00:00,收入,****3,155******65,闲鱼虚拟资料,收入,30.00,,交易成功,TXN-OV-JUL2,,",
    ]
    path = settings.data_dir / "ov.csv"
    path.write_bytes(
        ("\n".join(["----导出----", "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注"] + rows)).encode("gb18030")
    )
    import_file(settings.db_path, path, "alipay")
    from app.decisions.engine import process_batch
    from app.ledger_repo import list_import_batches

    process_batch(settings.db_path, list_import_batches(settings.db_path)[0]["id"])
    for group in group_review_items(settings.db_path):
        item = group.items[0]
        category = item.suggested_category
        if group.counterparty == "某航司":
            category = "旅游"  # 机票 → 旅游类（单独展示）
        confirm_group(
            settings.db_path, group.counterparty, group.platform,
            entry_type=item.suggested_type or TYPE_CONSUMPTION,
            category=category or "日常三餐",
        )

    stats = overview_stats(settings.db_path, "2026-07")
    # 旅游/副业收入/其他收入不混入日常环比
    assert stats["travel_cents"] == 80000
    assert stats["total_income_cents"] == 3000
    assert stats["daily_current_cents"] == 0  # 7 月无日常五类
    assert stats["daily_change_pct"] == -100.0  # 上月 10 元 → 0 元 = -100%
    # 首页渲染
    response = c.get("/", params={"ym": "2026-07"})
    assert response.status_code == 200
    assert "总消费" in response.text


# ── 全流程：一次导入 → 全部待办处理 → 统计收敛 ──


def test_e2e_full_pipeline(client):
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")
    _upload(c, WECHAT_SAMPLE, "wechat")

    # 处理分类区（含食堂批量确认、圆明园消费、闲鱼收入等）
    _confirm_groups(settings)
    # 关联拼多多退款与圆明园退款
    sources = _source_ids(settings)
    ledger = _ledger_by_source(settings)
    for refund_txn, consumption_txn in [
        ("AP-20260709-001_RM001", "AP-20260709-001"),
        ("WX-005", "WX-001"),
    ]:
        if refund_txn in sources and consumption_txn in ledger:
            link_refund_to_ledger(settings.db_path, sources[refund_txn], ledger[consumption_txn])

    stats = overview_stats(settings.db_path, "2026-07")
    # 消费：食堂 31.00（净）+ 滴滴 20.00 + 拼多多 0（全退）+ 圆明园 0（全退）
    assert stats["total_consumption_cents"] == 5100
    # 待确认：提现、人际、无原消费退款（剩余高风险）
    remaining = [r["reason"] for r in list_review_queue(settings.db_path)]
    assert set(remaining) <= {"withdrawal", "person_transfer", "refund_pending"}


# ── 红队 15：页面级无效输入不写库、不 500（规格 §8） ──


def test_e2e_manual_entry_invalid_date_no_persist_no_500(client):
    """P1：非法日期补账不落库、不返回 500。"""
    c, settings = client
    before = len(list_ledger_entries(settings.db_path))
    response = c.post(
        "/transactions",
        data={"entry_type": "consumption", "amount": "12.34", "category": "x", "txn_date": "garbage", "note": ""},
    )
    assert response.status_code in (200, 400)
    assert len(list_ledger_entries(settings.db_path)) == before  # 未写入
    stats = overview_stats(settings.db_path, "2026-07")
    assert stats["total_consumption_cents"] == 0  # 无坏数据
    assert "YYYY-MM-DD" in response.text or "must be" in response.text


def test_manual_invalid_date_rejected_at_invalid_calendar_date(client):
    """非法日历日期（如 2 月 30 日）同样不落库。"""
    c, settings = client
    before = len(list_ledger_entries(settings.db_path))
    response = c.post(
        "/transactions",
        data={"entry_type": "income", "amount": "1.00", "category": "", "txn_date": "2026-02-30", "note": ""},
    )
    assert response.status_code in (200, 400)
    assert len(list_ledger_entries(settings.db_path)) == before


def test_manual_invalid_entry_type_not_persisted(client):
    c, settings = client
    before = len(list_ledger_entries(settings.db_path))
    response = c.post(
        "/transactions",
        data={"entry_type": "steal", "amount": "1.00", "category": "", "txn_date": "2026-07-01", "note": ""},
    )
    assert response.status_code == 200
    assert "补记失败" in response.text
    assert len(list_ledger_entries(settings.db_path)) == before


def test_manual_invalid_amount_not_persisted(client):
    c, settings = client
    before = len(list_ledger_entries(settings.db_path))
    response = c.post(
        "/transactions",
        data={"entry_type": "consumption", "amount": "abc", "category": "", "txn_date": "2026-07-01", "note": ""},
    )
    assert response.status_code == 200
    assert "补记失败" in response.text
    assert len(list_ledger_entries(settings.db_path)) == before


def test_import_invalid_platform_no_500(client):
    """P2：非法平台导入不返回 500。"""
    c, _ = client
    response = c.post(
        "/imports/new",
        data={"platform": "evil"},
        files={"file": ("x.csv", b"not-a-real-bill", "text/csv")},
    )
    assert response.status_code == 200
    assert "无效平台" in response.text


def test_rule_invalid_field_no_500(client):
    """P2：非法规则字段不返回 500。"""
    c, _ = client
    response = c.post(
        "/rules",
        data={"match_field": "evil", "match_pattern": "x", "target_type": "consumption", "target_category": ""},
    )
    assert response.status_code == 200
    assert "无效匹配字段" in response.text


def test_rule_invalid_type_no_500(client):
    c, _ = client
    response = c.post(
        "/rules",
        data={"match_field": "counterparty", "match_pattern": "x", "target_type": "hack", "target_category": ""},
    )
    assert response.status_code == 200
    assert "无效目标类型" in response.text


def test_delete_refund_linked_via_http_no_500(client):
    """已退款消费删除经 HTTP → 正常提示非 500，数据保留。"""
    c, settings = client
    rows = [
        "2026-07-10 10:00:00,日用百货,某店,/,消费,支出,50.00,余额宝,交易成功,TXN-HT-1,,",
        "2026-07-11 10:00:00,日用百货,某店,/,退款-消费,不计收支,50.00,余额宝,退款成功,TXN-HT-1_RM1,,",
    ]
    path = settings.data_dir / "del.csv"
    path.write_bytes(
        ("\n".join(["----导出----", "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注"] + rows)).encode("gb18030")
    )
    from app.importing.service import import_file
    from app.decisions.engine import process_batch

    r = import_file(settings.db_path, path, "alipay")
    process_batch(settings.db_path, r.batch_id)
    _confirm_groups(settings)
    sources = _source_ids(settings)
    ledger = _ledger_by_source(settings)
    link_refund_to_ledger(settings.db_path, sources["TXN-HT-1_RM1"], ledger["TXN-HT-1"])
    refunded_entry = next(e for e in list_ledger_entries(settings.db_path) if int(e["amount_cents"]) == 5000)

    response = c.post(f"/transactions/{refunded_entry['id']}/delete", follow_redirects=False)
    assert response.status_code in (200, 303)
    assert list_ledger_entries(settings.db_path)  # 记录仍在


# ── 红队 16：损坏微信 XLSX 上传不返回 500、无批次残留、临时文件清理 ──


def test_import_corrupt_wechat_xlsx_nonzip_no_500_no_residue(client):
    """P2：损坏微信 XLSX（非 ZIP 字节）→ 错误页而非 500；无批次残留；临时文件清理。"""
    c, settings = client
    from app.stats import list_batches

    before = len(list_batches(settings.db_path))
    response = c.post(
        "/imports/new",
        data={"platform": "wechat"},
        files={"file": ("bad.xlsx", b"this is not a zip archive", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "微信账单文件损坏" in response.text
    assert len(list_batches(settings.db_path)) == before  # 无批次残留
    leftover = [p.name for p in settings.data_dir.iterdir() if p.name != "t.sqlite"]
    assert leftover == []  # 上传临时文件已清理


# ── 红队 17：账单行交易时间校验的页面级端到端 ──


def test_import_bad_alipay_date_via_http_no_residue(client):
    """P1：结构合法但交易时间非法的支付宝 CSV → 错误页而非 500；无批次/无来源残留。"""
    c, settings = client
    import openpyxl

    from app.stats import list_batches

    csv = (
        "----------------导出信息----------------\n"
        "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注\n"
        "not-a-date,日用百货,某店,/,消费,支出,10.00,余额宝,交易成功,BAD-DATE-1,,"
    ).encode("gb18030")

    before_batches = len(list_batches(settings.db_path))
    response = c.post(
        "/imports/new",
        data={"platform": "alipay"},
        files={"file": ("bad-date.csv", csv, "text/csv")},
    )
    assert response.status_code == 200
    assert "invalid occurred_at" in response.text
    assert len(list_batches(settings.db_path)) == before_batches  # 无批次残留
    assert list_source_transactions(settings.db_path, platform="alipay") == []  # 无来源残留


def test_import_bad_wechat_date_via_http_no_residue(client):
    """P1：交易时间非法的微信 XLSX → 错误页而非 500；无批次/无来源残留。"""
    c, settings = client
    import openpyxl

    from app.stats import list_batches

    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(
        [
            "交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
            "支付方式", "当前状态", "交易单号", "商户单号", "备注",
        ]
    )
    ws.append(["not-a-date", "商户消费", "某店", "x", "支出", 10, "零钱通", "支付成功", "WX-BAD-1", "M1", "/"])
    wb.save(buf)

    before_batches = len(list_batches(settings.db_path))
    response = c.post(
        "/imports/new",
        data={"platform": "wechat"},
        files={"file": ("bad-date.xlsx", buf.getvalue(), "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "invalid occurred_at" in response.text
    assert len(list_batches(settings.db_path)) == before_batches  # 无批次残留
    assert list_source_transactions(settings.db_path, platform="wechat") == []  # 无来源残留


# ── 红队 18：空交易时间行不得静默丢弃（页面级端到端）──


def test_import_empty_alipay_date_via_http_no_residue(client):
    """P1：首列为空但其余字段有效的支付宝 CSV → 错误页而非静默成功；无批次/无来源/临时文件清理。"""
    c, settings = client

    from app.stats import list_batches

    csv = (
        "----------------导出信息----------------\n"
        "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注\n"
        ",日用百货,某店,/,消费,支出,10.00,余额宝,交易成功,EMPTY-DATE-1,,"
    ).encode("gb18030")

    before_batches = len(list_batches(settings.db_path))
    response = c.post(
        "/imports/new",
        data={"platform": "alipay"},
        files={"file": ("empty-date.csv", csv, "text/csv")},
    )
    assert response.status_code == 200
    assert "invalid occurred_at" in response.text
    assert len(list_batches(settings.db_path)) == before_batches  # 无批次残留
    assert list_source_transactions(settings.db_path, platform="alipay") == []  # 无来源残留
    leftover = [p.name for p in settings.data_dir.iterdir() if p.name != "t.sqlite"]
    assert leftover == []  # 上传临时文件已清理


def test_import_empty_wechat_date_via_http_no_residue(client):
    """P1：首格为空但其余字段有效的微信 XLSX → 错误页而非静默成功；无批次/无来源/临时文件清理。"""
    c, settings = client
    import openpyxl

    from app.stats import list_batches

    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(
        [
            "交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
            "支付方式", "当前状态", "交易单号", "商户单号", "备注",
        ]
    )
    ws.append([None, "商户消费", "某店", "x", "支出", 10, "零钱通", "支付成功", "WX-EMPTY-1", "M1", "/"])
    wb.save(buf)

    before_batches = len(list_batches(settings.db_path))
    response = c.post(
        "/imports/new",
        data={"platform": "wechat"},
        files={"file": ("empty-date.xlsx", buf.getvalue(), "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "invalid occurred_at" in response.text
    assert len(list_batches(settings.db_path)) == before_batches  # 无批次残留
    assert list_source_transactions(settings.db_path, platform="wechat") == []  # 无来源残留
    leftover = [p.name for p in settings.data_dir.iterdir() if p.name != "t.sqlite"]
    assert leftover == []  # 上传临时文件已清理


def test_import_corrupt_wechat_xlsx_bad_xml_no_500_no_residue(client):
    """P2：ZIP 内 XML 损坏（伪造工作簿）→ 错误页而非 500；无批次残留。"""
    c, settings = client
    import zipfile

    from app.stats import list_batches

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("workbook.xml", "<broken-")
    payload = buf.getvalue()

    before = len(list_batches(settings.db_path))
    response = c.post(
        "/imports/new",
        data={"platform": "wechat"},
        files={"file": ("fake.xlsx", payload, "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "微信账单文件损坏" in response.text
    assert len(list_batches(settings.db_path)) == before  # 无批次残留
    leftover = [p.name for p in settings.data_dir.iterdir() if p.name != "t.sqlite"]
    assert leftover == []  # 上传临时文件已清理
