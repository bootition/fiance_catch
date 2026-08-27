"""Inbox UX 优化测试：HTMX 局部刷新片段、退款候选刷新路由、无候选指引（2026-08-27）。"""

import io

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import init_db
from app.decisions.constants import CATEGORY_DAILY_MEALS, TYPE_CONSUMPTION
from app.importing.service import import_file
from app.ledger_repo import list_ledger_entries, list_review_queue
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
    assert 'id="category-table-area"' in text
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


def test_inbox_high_risk_pagination(client):
    """高风险区超过每页上限时分页：第 1 页 20 条，翻页局部刷新出剩余条数。"""
    c, settings = client
    rows = [
        f"2026-07-{day:02d} 10:00:00,账户存取,某银行,/,提现到银行卡,不计收支,500.00,账户余额,交易成功,TXN-PG-{day},,"
        for day in range(1, 26)
    ]
    _upload(c, rows)
    page1 = c.get("/inbox")
    assert page1.status_code == 200
    assert page1.text.count("card-inner") == 20
    assert "第 1 / 2 页" in page1.text
    assert "共 25 条" in page1.text
    # 翻页：局部刷新只返回高风险区 section
    page2 = c.get("/inbox/high-risk", params={"page": 2})
    assert page2.status_code == 200
    assert page2.text.count("card-inner") == 5
    assert "第 2 / 2 页" in page2.text
    assert "<html" not in page2.text


def test_inbox_category_pagination_and_search(client):
    """分类区超过每页上限时分页，且支持按商户名模糊搜索。"""
    c, settings = client
    rows = [
        f"2026-07-01 10:{i:02d}:00,日用百货,商户{i:02d},/,消费,支出,10.00,余额宝,交易成功,TXN-CAT-{i},,"
        for i in range(1, 41)
    ]
    _upload(c, rows)
    page1 = c.get("/inbox")
    assert page1.status_code == 200
    # 整页含搜索框 form + 30 个分类行 form（每页 30 组）
    assert page1.text.count('<form method="post"') == 30
    assert "第 1 / 2 页" in page1.text
    assert "共 40 组" in page1.text
    # 翻页：第 2 页 10 组
    page2 = c.get("/inbox/category", params={"page": 2})
    assert page2.status_code == 200
    assert page2.text.count('<form method="post"') == 10
    assert "第 2 / 2 页" in page2.text
    assert "<html" not in page2.text
    # 搜索：按商户名过滤
    result = c.get("/inbox/category", params={"q": "商户05"})
    assert result.status_code == 200
    assert result.text.count('<form method="post"') == 1
    assert "商户05" in result.text
    assert "商户06" not in result.text


def test_inbox_search_enter_is_intercepted_and_searches_item_desc(client):
    """搜索回车不再触发浏览器整页 GET：hx-trigger 显式包含 submit，且搜索范围包含商品说明。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-01 10:00:00,收入,x***1,/,闲鱼虚拟资料项目,收入,10.00,余额宝,交易成功,TXN-SEARCH-1,,",
            "2026-07-02 10:00:00,收入,x***2,/,普通商品,收入,20.00,余额宝,交易成功,TXN-SEARCH-2,,",
        ],
    )
    inbox = c.get("/inbox")
    assert 'hx-trigger="input changed delay:300ms, search, submit"' in inbox.text
    result = c.get("/inbox/category", params={"q": "闲鱼"})
    assert result.status_code == 200
    assert "<html" not in result.text  # 局部片段而非整页
    assert "闲鱼虚拟资料项目" in result.text
    assert "普通商品" not in result.text


def test_inbox_category_rows_show_details_and_formal_category_options(client):
    """分类区展示商品说明样本与交易时间，且消费/收入分类下拉内置 PRD 正式分类。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-05 18:24:30,收入,x***1,/,闲鱼虚拟资料项目A,收入,10.00,余额宝,交易成功,TXN-DETAIL-1,,",
            "2026-07-06 18:25:30,收入,x***1,/,闲鱼虚拟资料项目B,收入,20.00,余额宝,交易成功,TXN-DETAIL-2,,",
        ],
    )
    inbox = c.get("/inbox")
    text = inbox.text
    assert "商品 / 说明（样本）" in text
    assert "最近交易时间" in text
    assert "闲鱼虚拟资料项目A" in text
    assert "闲鱼虚拟资料项目B" in text
    assert "2026-07-06 18:25:30" in text
    assert "查看 2 笔明细" in text
    # PRD §2.2 正式分类必须内置于下拉，而不是只有“选择分类”
    assert 'optgroup label="消费分类"' in text
    assert 'optgroup label="收入分类"' in text
    for category in ("日常三餐", "出行交通", "书籍学习", "日常娱乐", "旅游", "日常缴费", "医疗健康", "副业成本", "副业收入", "其他收入"):
        assert f'<option value="{category}"' in text


def test_inbox_pagination_has_page_numbers_and_jump_controls(client):
    """两个分页区都显示当前页附近的页码按钮，并提供页码输入跳转。"""
    c, settings = client
    risk_rows = [
        f"2026-07-{day:02d} 10:00:00,账户存取,某银行,/,提现到银行卡,不计收支,500.00,账户余额,交易成功,TXN-PGNUM-R{day},,"
        for day in range(1, 26)
    ]
    _upload(c, risk_rows)
    inbox = c.get("/inbox")
    assert "pagination-current" in inbox.text
    assert "跳至" in inbox.text
    assert 'name="page"' in inbox.text
    assert "跳转" in inbox.text

    risk_page2 = c.get("/inbox/high-risk", params={"page": 2})
    assert risk_page2.status_code == 200
    assert 'aria-current="page">2</span>' in risk_page2.text
    assert "pagination-jump" in risk_page2.text

    cat_rows = [
        f"2026-07-01 10:{i % 60:02d}:00,日用百货,商户{i:02d},/,消费,支出,10.00,余额宝,交易成功,TXN-PGNUM-C{i},,"
        for i in range(1, 41)
    ]
    _upload(c, cat_rows)
    cat_page1 = c.get("/inbox/category", params={"page": 1})
    assert 'aria-current="page">1</span>' in cat_page1.text
    assert 'hx-get="/inbox/category?page=2"' in cat_page1.text
    assert "pagination-jump" in cat_page1.text


def test_inbox_transfer_confirm_requires_transfer_category(client):
    """调拨使用独立分类，必须选择攒股收息/哈利布朗/虚拟货币/CS饰品之一。"""
    from app.ledger_repo import create_classification_rule

    c, settings = client
    create_classification_rule(
        settings.db_path,
        match_field="counterparty",
        match_pattern="转款商户",
        target_type="transfer",
        target_category="",
    )
    _upload(
        c,
        [
            "2026-07-01 10:00:00,其他,转款商户,/,转款事项,支出,100.00,余额宝,交易成功,TXN-TRANSFER-1,,",
            "2026-07-02 10:00:00,其他,转款商户,/,转款事项,支出,50.00,余额宝,交易成功,TXN-TRANSFER-2,,",
        ],
    )
    response = c.post(
        "/inbox/confirm",
        data={
            "counterparty": "转款商户",
            "platform": "alipay",
            "direction": "expense",
            "entry_type": "transfer",
            "category": "攒股收息",
        },
    )
    assert response.status_code == 200
    assert "已确认 2 项" in response.text


def test_inbox_direction_locks_entry_type(client):
    """账单方向已知：收入组不能选择支出，服务端同步拒绝。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-01 10:00:00,收入,x***1,/,闲鱼虚拟资料,收入,10.00,余额宝,交易成功,TXN-DIR-1,,",
            "2026-07-02 10:00:00,收入,x***1,/,闲鱼虚拟资料,收入,20.00,余额宝,交易成功,TXN-DIR-2,,",
        ],
    )
    inbox = c.get("/inbox")
    text = inbox.text
    # 方向已知时 UI 不提供方向/类型选择，直接锁定为“收入”
    assert 'name="entry_type" value="income"' in text
    assert '>收入</span>' in text or '收入' in text

    wrong = c.post(
        "/inbox/confirm",
        data={
            "counterparty": "x***1",
            "platform": "alipay",
            "direction": "income",
            "entry_type": "consumption",
            "category": "日常三餐",
        },
    )
    assert wrong.status_code == 200
    assert "失败" in wrong.text
    assert "收入方向" in wrong.text

    right = c.post(
        "/inbox/confirm",
        data={
            "counterparty": "x***1",
            "platform": "alipay",
            "direction": "income",
            "entry_type": "income",
            "category": "副业收入",
        },
    )
    assert "已确认 2 项" in right.text


def test_inbox_single_item_confirm_inside_group(client):
    """分组只按商户×平台×方向；组内每笔可单独处理，其他笔保持待确认。"""
    c, settings = client
    _upload(
        c,
        [
            "2026-07-01 10:00:00,收入,x***9,/,闲鱼资料A,收入,10.00,余额宝,交易成功,TXN-SINGLE-1,,",
            "2026-07-02 10:00:00,收入,x***9,/,闲鱼资料B,收入,20.00,余额宝,交易成功,TXN-SINGLE-2,,",
            "2026-07-03 10:00:00,收入,x***9,/,闲鱼资料C,收入,30.00,余额宝,交易成功,TXN-SINGLE-3,,",
        ],
    )
    inbox = c.get("/inbox")
    assert "合并规则" in inbox.text
    assert "商户/交易对方 + 平台 + 收支方向" in inbox.text
    assert inbox.text.count("/inbox/item-form/") == 3

    review_id = _review_id_by_txn(settings, "TXN-SINGLE-2")
    form = c.get(f"/inbox/item-form/{review_id}?cat_page=1&cat_q=")
    assert form.status_code == 200
    assert 'id="category-table-area"' not in form.text  # 只返回单笔表单片段
    assert "确认此笔" in form.text

    response = c.post(
        "/inbox/confirm-item",
        data={
            "review_id": review_id,
            "entry_type": "income",
            "category": "副业收入",
            "cat_page": 1,
            "cat_q": "",
        },
    )
    assert response.status_code == 200
    assert f"已单独确认 #{review_id}" in response.text
    assert len(list_ledger_entries(settings.db_path)) == 1
    pending = list_review_queue(settings.db_path)
    assert len(pending) == 2
    assert "确认 2 项" in response.text

    # 已处理单笔的表单不再可用
    gone = c.get(f"/inbox/item-form/{review_id}")
    assert "该笔已处理" in gone.text


def test_inbox_has_scroll_anchor_script(client):
    """处理后保持原滚动位置：模板包含视口锚定脚本。"""
    c, _ = client
    inbox = c.get("/inbox")
    assert "pendingScrollAnchor" in inbox.text
    assert "getBoundingClientRect" in inbox.text
    assert "overflow-anchor" not in inbox.text  # CSS 中声明
