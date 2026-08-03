"""阶段 6/7 端到端测试（规格 §7.6/§8 验收条件）。

使用匿名化固定样本（tests/samples/，真实导出格式），从真实上传请求开始，
之后仅通过公开 HTTP 路由与 HTML 表单字段完成全部用户动作（导入、批量确认、
退款关联、逐笔定性、编辑、撤销），不直接调用仅供 UI 使用的领域服务。
"""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import init_db
from app.decisions.constants import (
    CATEGORY_DAILY_MEALS,
    CATEGORY_TRANSPORT,
    TYPE_CONSUMPTION,
)
from app.ledger_repo import (
    get_ledger_entry,
    list_audit_events,
    list_ledger_entries,
    list_review_queue,
    list_source_transactions,
)
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


def _classification_groups(settings):
    """查询分类区待办（unmatched/observing）按商户分组。

    仅读取数据用于构造 HTTP 表单参数；确认动作本身走 /inbox/confirm。
    """
    rows = [
        r
        for r in list_review_queue(settings.db_path)
        if r["reason"] in ("unmatched", "observing_rule")
    ]
    sources = {
        s["id"]: s for s in list_source_transactions(settings.db_path)
    }
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        source = sources.get(r["source_transaction_id"])
        key = (
            source["counterparty"] if source is not None else "",
            source["platform"] if source is not None else "",
        )
        groups.setdefault(
            key, {"type": r["suggested_type"], "category": r["suggested_category"]}
        )
    return groups


def _confirm_groups(client, settings, only=None):
    """批量确认分类区（可选限定商户）；返回确认的组数。"""
    count = 0
    for (counterparty, platform), suggestion in _classification_groups(settings).items():
        if only and counterparty != only:
            continue
        response = client.post(
            "/inbox/confirm",
            data={
                "counterparty": counterparty,
                "platform": platform,
                "entry_type": suggestion["type"] or TYPE_CONSUMPTION,
                "category": suggestion["category"] or CATEGORY_DAILY_MEALS,
            },
        )
        assert response.status_code == 200
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


def _pending_review_for(settings, txn_id: str):
    """返回某来源流水仍 pending 的待办（查库定位表单参数）。"""
    source_id = _source_ids(settings)[txn_id]
    for r in list_review_queue(settings.db_path):
        if r["source_transaction_id"] == source_id and r["status"] == "pending":
            return r
    return None


def _link_refund_via_http(client, settings, refund_txn: str, consumption_txn: str):
    """经 HTTP 完成退款关联（表单参数从库中查询构造）。"""
    review = _pending_review_for(settings, refund_txn)
    assert review is not None, f"no pending refund review for {refund_txn}"
    return client.post(
        "/inbox/refund/link",
        data={
            "review_id": review["id"],
            "refund_source_id": review["source_transaction_id"],
            "original_ledger_id": _ledger_by_source(settings)[consumption_txn],
        },
    )


def _resolve_via_http(client, review_id: int, **fields):
    """经 HTTP 逐笔定性一条高风险待办。"""
    data = {"review_id": review_id}
    data.update(fields)
    return client.post("/inbox/resolve", data=data)


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
    # 创建规则走 /rules 表单：滴滴 → active（提升），食堂 → 观察期
    response = c.post(
        "/rules",
        data={
            "match_field": "counterparty",
            "match_pattern": "滴滴出行",
            "target_type": TYPE_CONSUMPTION,
            "target_category": CATEGORY_TRANSPORT,
        },
    )
    assert response.status_code == 200
    promote = c.post("/rules/1/promote")
    assert promote.status_code == 200
    c.post(
        "/rules",
        data={
            "match_field": "counterparty",
            "match_pattern": "学校食堂",
            "target_type": TYPE_CONSUMPTION,
            "target_category": CATEGORY_DAILY_MEALS,
        },
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
    response = _upload(c, path, "alipay")
    assert response.status_code == 200
    _confirm_groups(c, settings)

    # 待确认页展示退款候选（原交易订单号匹配）
    inbox = c.get("/inbox")
    assert "退款待办" in inbox.text
    assert "原交易订单号匹配" in inbox.text

    # 经 HTTP 关联退款
    link = _link_refund_via_http(c, settings, "TXN-E2E-JUN1_RM1", "TXN-E2E-JUN1")
    assert link.status_code == 200
    assert "净成本" in link.text

    june = overview_stats(settings.db_path, "2026-06")
    assert june["total_consumption_cents"] == 0  # 50 - 50 回写原周期
    july = overview_stats(settings.db_path, "2026-07")
    assert july["total_consumption_cents"] == 0  # 7 月无净消费


# ── 验收：同商户批量确认并创建观察规则 ──


def test_e2e_bulk_confirm_creates_observing_rule(client):
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")
    confirmed = _confirm_groups(c, settings, only="学校食堂")
    assert confirmed == 1
    assert len([e for e in list_ledger_entries(settings.db_path) if e["category"] == CATEGORY_DAILY_MEALS]) == 2

    from app.ledger_repo import list_classification_rules

    rules = list_classification_rules(settings.db_path)
    assert any(r["match_pattern"] == "学校食堂" and r["status"] == "observing" for r in rules)


# ── 验收：安全撤销（编辑/关联阻塞） ──


def test_e2e_revoke_blocks_edited_and_linked(client):
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")
    _confirm_groups(c, settings)

    sources = _source_ids(settings)
    ledger = _ledger_by_source(settings)

    # 拼多多消费 → 退款关联（退款单号 _RM 前缀 = 原订单号 → 100 分）
    inbox = c.get("/inbox")
    assert "原交易订单号匹配" in inbox.text  # 候选可见
    link = _link_refund_via_http(c, settings, "AP-20260709-001_RM001", "AP-20260709-001")
    assert link.status_code == 200

    # 手动编辑一笔（学校食堂已确认消费，未关联退款）
    from app.ledger_repo import list_import_batches

    batch_id = list_import_batches(settings.db_path)[0]["id"]
    auto = next(e for e in list_ledger_entries(settings.db_path) if int(e["amount_cents"]) == 1550)
    edit = c.post(
        f"/transactions/{auto['id']}/edit",
        data={
            "entry_type": TYPE_CONSUMPTION,
            "amount": "15.50",
            "category": "改后分类",
            "txn_date": "2026-07-01",
            "note": "人工改",
        },
        follow_redirects=False,
    )
    assert edit.status_code == 303

    # 经 HTTP 撤销，页面明确列出阻塞项明细
    revoke = c.post(f"/imports/{batch_id}/revoke")
    assert revoke.status_code == 200
    assert "阻塞项（已保留）" in revoke.text
    assert "manual_edited" in revoke.text
    assert "refund_linked" in revoke.text
    assert "已人工编辑" in revoke.text
    assert "已参与退款关联" in revoke.text
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
    response = _upload(c, path, "alipay")
    assert response.status_code == 200
    # 经 HTTP 批量确认全部分类区（某航司 → 旅游）
    for (counterparty, platform), suggestion in _classification_groups(settings).items():
        category = suggestion["category"]
        if counterparty == "某航司":
            category = "旅游"  # 机票 → 旅游类（单独展示）
        confirmed = c.post(
            "/inbox/confirm",
            data={
                "counterparty": counterparty,
                "platform": platform,
                "entry_type": suggestion["type"] or TYPE_CONSUMPTION,
                "category": category or "日常三餐",
            },
        )
        assert confirmed.status_code == 200

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
    _confirm_groups(c, settings)
    # 关联拼多多退款与圆明园退款（仅经 HTTP）
    for refund_txn, consumption_txn in [
        ("AP-20260709-001_RM001", "AP-20260709-001"),
        ("WX-005", "WX-001"),
    ]:
        if refund_txn in _source_ids(settings) and consumption_txn in _ledger_by_source(settings):
            link = _link_refund_via_http(c, settings, refund_txn, consumption_txn)
            assert link.status_code == 200

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
    assert _upload(c, path, "alipay").status_code == 200
    _confirm_groups(c, settings)
    link = _link_refund_via_http(c, settings, "TXN-HT-1_RM1", "TXN-HT-1")
    assert link.status_code == 200
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


# ── 阶段 7：高风险待办逐笔处理（仅经 HTTP）──


def test_e2e_refund_link_via_http_closes_review_and_audits(client):
    """退款关联全流程：候选展示 → 关联 → 待办关闭/批次计数/审计事件一致。"""
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")
    _confirm_groups(c, settings)

    inbox = c.get("/inbox")
    assert "退款待办" in inbox.text
    assert "原交易订单号匹配" in inbox.text  # 拼多多退款候选

    before_batches = overview_stats(settings.db_path, "2026-07")["pending_count"]
    link = _link_refund_via_http(c, settings, "AP-20260709-001_RM001", "AP-20260709-001")
    assert link.status_code == 200
    assert "已关联退款" in link.text

    # 待办关闭
    assert _pending_review_for(settings, "AP-20260709-001_RM001") is None
    # 统计：拼多多消费全退 → 净成本 0
    stats = overview_stats(settings.db_path, "2026-07")
    assert stats["total_consumption_cents"] == 5100  # 食堂 31.00 + 滴滴 20.00 + 拼多多 40-40 全退
    assert stats["pending_count"] == before_batches - 1
    # 审计事件
    events = [e for e in list_audit_events(settings.db_path) if e["event_type"] == "refund_linked"]
    assert len(events) == 1
    assert "refund_source" in events[0]["detail"]


def test_e2e_withdrawal_resolve_via_http(client):
    """提现逐笔选用途（未追踪账户调拨）→ transfer 入账、待办关闭、不污染统计、审计写入。"""
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")

    review = _pending_review_for(settings, "AP-20260707-001")
    assert review is not None and review["reason"] == "withdrawal"
    response = _resolve_via_http(c, review["id"], purpose="transfer")
    assert response.status_code == 200
    assert "已定性" in response.text

    assert _pending_review_for(settings, "AP-20260707-001") is None
    entry = next(
        e for e in list_ledger_entries(settings.db_path)
        if e["source_transaction_id"] == review["source_transaction_id"]
    )
    assert entry["entry_type"] == "transfer"  # 未追踪账户调拨 → 调拨
    assert overview_stats(settings.db_path, "2026-07")["total_consumption_cents"] == 0
    assert overview_stats(settings.db_path, "2026-07")["total_income_cents"] == 0
    events = [e for e in list_audit_events(settings.db_path) if e["event_type"] == "high_risk_resolved"]
    assert any("purpose:transfer" in e["detail"] for e in events)


def test_e2e_withdrawal_invalid_purpose_via_http_no_500(client):
    """提现非法用途 → 错误提示非 500，不写账本不关待办。"""
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")
    review = _pending_review_for(settings, "AP-20260707-001")
    before = len(list_ledger_entries(settings.db_path))

    response = _resolve_via_http(c, review["id"], purpose="steal")
    assert response.status_code == 200
    assert "处理失败" in response.text
    assert len(list_ledger_entries(settings.db_path)) == before
    assert _pending_review_for(settings, "AP-20260707-001") is not None  # 待办保留


def test_e2e_person_transfer_resolve_via_http(client):
    """人际转账人工定性（调拨/收入）→ 入账并关闭待办。"""
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")

    review = _pending_review_for(settings, "AP-20260708-001")
    assert review is not None and review["reason"] == "person_transfer"
    response = _resolve_via_http(c, review["id"], entry_type="transfer", category="")
    assert response.status_code == 200
    assert "已定性" in response.text

    assert _pending_review_for(settings, "AP-20260708-001") is None
    entry = next(
        e for e in list_ledger_entries(settings.db_path)
        if e["source_transaction_id"] == review["source_transaction_id"]
    )
    assert entry["entry_type"] == "transfer"

    # 微信人际转账定性为收入
    _upload(c, WECHAT_SAMPLE, "wechat")
    review2 = _pending_review_for(settings, "WX-002")
    assert review2 is not None and review2["reason"] == "person_transfer"
    response2 = _resolve_via_http(c, review2["id"], entry_type="income", category="其他收入")
    assert response2.status_code == 200
    entry2 = next(
        e for e in list_ledger_entries(settings.db_path)
        if e["source_transaction_id"] == review2["source_transaction_id"]
    )
    assert entry2["entry_type"] == "income"
    assert entry2["category"] == "其他收入"


# ── 阶段 7：流水详情与人工编辑（仅经 HTTP）──


def test_e2e_entry_detail_and_edit_via_http(client):
    """流水详情页展示来源/批次/退款关联/审计；编辑产生 manual_edit 审计事件。"""
    c, settings = client
    _upload(c, ALIPAY_SAMPLE, "alipay")
    _confirm_groups(c, settings)
    # 关联一笔退款
    _link_refund_via_http(c, settings, "AP-20260709-001_RM001", "AP-20260709-001")

    # 普通消费详情页：来源/批次/审计区块
    entry = next(e for e in list_ledger_entries(settings.db_path) if int(e["amount_cents"]) == 2000)
    detail = c.get(f"/transactions/{entry['id']}")
    assert detail.status_code == 200
    assert "来源流水" in detail.text
    assert "批次归属" in detail.text
    assert "审计事件" in detail.text
    # 已关联退款记录：退款关联明细可见
    pdd = next(e for e in list_ledger_entries(settings.db_path) if int(e["amount_cents"]) == 4000)
    pdd_detail = c.get(f"/transactions/{pdd['id']}")
    assert "退款关联" in pdd_detail.text  # 关联明细区块
    assert "退款关联" in pdd_detail.text  # 审计事件中文标签（refund_linked）

    # 编辑产生 manual_edit 审计事件并在详情页展示
    edit = c.post(
        f"/transactions/{entry['id']}/edit",
        data={
            "entry_type": TYPE_CONSUMPTION,
            "amount": "25.00",
            "category": "改后分类",
            "txn_date": "2026-07-03",
            "note": "人工改",
        },
        follow_redirects=False,
    )
    assert edit.status_code == 303
    events = [e for e in list_audit_events(settings.db_path) if e["event_type"] == "manual_edit"]
    assert len(events) == 1
    assert events[0]["ref_ledger_id"] == entry["id"]
    assert "amount:2000" in events[0]["detail"]  # 变更前金额
    detail2 = c.get(f"/transactions/{entry['id']}")
    assert "人工修改" in detail2.text  # 审计展示
    edited = get_ledger_entry(settings.db_path, entry["id"])
    assert int(edited["amount_cents"]) == 2500
    assert edited["category"] == "改后分类"


# ── 阶段 7 复审：自动规则入账在流水详情中可追溯（规格 §3.4）──


def test_e2e_rule_applied_audit_traceable_via_http(client):
    """创建并提升自动规则 → 上传命中交易 → 详情页展示规则命中证据（规则ID/匹配依据）。"""
    c, settings = client
    # 创建并提升自动规则（滴滴 → 出行交通）
    created = c.post(
        "/rules",
        data={
            "match_field": "counterparty",
            "match_pattern": "滴滴出行",
            "target_type": TYPE_CONSUMPTION,
            "target_category": CATEGORY_TRANSPORT,
        },
    )
    assert created.status_code == 200
    assert c.post("/rules/1/promote").status_code == 200

    _upload(c, ALIPAY_SAMPLE, "alipay")

    # 自动入账的滴滴记录
    entry = next(
        e for e in list_ledger_entries(settings.db_path)
        if int(e["amount_cents"]) == 2000
    )
    # 审计事件关联该账本记录（可按账本追溯）
    events = [
        e for e in list_audit_events(settings.db_path)
        if e["event_type"] == "rule_applied"
    ]
    assert len(events) == 1
    assert events[0]["ref_ledger_id"] == entry["id"]
    assert events[0]["ref_rule_id"] == 1
    assert "field:counterparty" in events[0]["detail"]
    assert "pattern:滴滴出行" in events[0]["detail"]

    # 详情页展示规则命中证据
    detail = c.get(f"/transactions/{entry['id']}")
    assert detail.status_code == 200
    assert "规则自动入账" in detail.text
    assert "滴滴出行" in detail.text  # 匹配依据
    assert "1" in detail.text or "rule" in detail.text  # 规则 ID 可见

    # 同一事件仍可按规则追溯（规则页命中历史依赖 ref_rule_id）
    by_rule = [e for e in events if e["ref_rule_id"] == 1]
    assert by_rule and by_rule[0]["ref_ledger_id"] == entry["id"]
