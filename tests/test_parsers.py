import datetime

import pytest
import openpyxl

from app.importing.alipay import parse_alipay_csv_bytes
from app.importing.model import RowStatus
from app.importing.wechat import parse_wechat_xlsx

ALIPAY_HEADER = (
    "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,"
    "收/付款方式,交易状态,交易订单号,商家订单号,备注"
)

ALIPAY_SAMPLE = "\n".join(
    [
        "-----------------------------导出信息-----------------------------",
        "导出信息：",
        "起始时间：[2025-08-01 00:00:00]",
        "共2笔记录",
        ALIPAY_HEADER,
        "2026-07-31 23:59:17,投资理财,余额宝,yue***@csfunds.com.cn,余额宝-单次转入,不计收支,86.73,账户余额,交易成功,20260731019130100010300080496457\t,LC123\t,,",
        "2026-07-31 19:21:36,日用百货,信美佳超市,/,信美佳超市消费,支出,25.00,余额宝,交易成功,2026073123001473301407202853\t,3212660000482200007895217399554438\t,,",
        "2026-07-31 11:35:24,收入,****3,155******65,框框老师高数资料,收入,1.29,,交易关闭,2026071323001153541401058464\t,T200P3312126554562063069\t,,",
        "2026-07-30 17:12:03,日用百货,拼多多平台商户,pdd***@yiran.com,退款-商户单号XP1626072801100159132957003010,不计收支,40.00,余额宝,退款成功,2026072823001473301440020461_RM0026073017201072916709004497\t,XP1626072801100159132957003010\t,,",
        "2026-07-28 01:17:06,日用百货,拼多多平台商户,pdd***@yiran.com,商户单号XP1626072801100159132957003010,支出,40.00,余额宝,交易关闭,2026072823001473301440020461\t,XP1626072801100159132957003010\t,,",
        "",
    ]
)


def test_alipay_parses_sample_rows():
    rows = parse_alipay_csv_bytes(ALIPAY_SAMPLE.encode("gb18030"))
    assert len(rows) == 5


def test_alipay_skips_header_and_empty_lines():
    rows = parse_alipay_csv_bytes(ALIPAY_SAMPLE.encode("gb18030"))
    statuses = [r.status for r in rows]
    assert statuses == [
        RowStatus.SUCCESS,
        RowStatus.SUCCESS,
        RowStatus.SKIPPED,
        RowStatus.REFUND,
        RowStatus.SKIPPED,
    ]


def test_alipay_normalizes_fields():
    rows = parse_alipay_csv_bytes(ALIPAY_SAMPLE.encode("gb18030"))
    neutral = rows[0]
    assert neutral.platform == "alipay"
    assert neutral.source_txn_id == "20260731019130100010300080496457"
    assert neutral.occurred_at == "2026-07-31 23:59:17"
    assert neutral.amount_cents == 8673
    assert neutral.direction == "neutral"
    assert neutral.counterparty == "余额宝"
    assert neutral.item_desc == "余额宝-单次转入"
    assert neutral.status_text == "交易成功"
    assert neutral.normalized_hash

    expense = rows[1]
    assert expense.direction == "expense"
    assert expense.amount_cents == 2500


def test_alipay_refund_detected():
    rows = parse_alipay_csv_bytes(ALIPAY_SAMPLE.encode("gb18030"))
    refund = rows[3]
    assert refund.status == RowStatus.REFUND
    assert refund.direction == "neutral"
    assert "退款" in refund.item_desc


def test_alipay_closed_skipped():
    rows = parse_alipay_csv_bytes(ALIPAY_SAMPLE.encode("gb18030"))
    assert rows[2].status == RowStatus.SKIPPED
    assert rows[4].status == RowStatus.SKIPPED


def test_alipay_missing_header_raises():
    with pytest.raises(ValueError):
        parse_alipay_csv_bytes("no header here".encode("utf-8"))


def test_alipay_item_with_comma_aligned():
    text = "\n".join(
        [
            ALIPAY_HEADER,
            '2026-07-31 12:00:00,其他,某商户,/,"商品A,商品B",支出,10.00,余额宝,交易成功,TXN1,,',
        ]
    )
    rows = parse_alipay_csv_bytes(text.encode("gb18030"))
    assert len(rows) == 1
    assert rows[0].item_desc == "商品A,商品B"


def _build_wechat_xlsx(tmp_path, rows):
    path = tmp_path / "wechat_sample.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    meta = [
        ["微信支付账单明细"],
        ["微信昵称：[测试用户]"],
        ["起始时间：[2025-08-01 00:00:00]"],
        ["导出类型：[全部]"],
        ["共3笔记录"],
        [None],
        ["----------------------微信支付账单明细列表--------------------"],
        [
            "交易时间",
            "交易类型",
            "交易对方",
            "商品",
            "收/支",
            "金额(元)",
            "支付方式",
            "当前状态",
            "交易单号",
            "商户单号",
            "备注",
        ],
    ]
    for row in meta:
        ws.append(row)
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def test_wechat_parses_sample_rows(tmp_path):
    rows = [
        [datetime.datetime(2026, 7, 30, 19, 56, 10), "转账", "王琦 (王大可)", "转账备注:微信转账", "支出", 70, "零钱通", "对方已收钱", "53010003298035202607300233568072", "1000050001202607300429226508877", "/"],
        [datetime.datetime(2026, 7, 20, 14, 14, 29), "商户消费", "圆明园遗址公园", "支付请求", "支出", 60, "零钱通", "支付成功", "4500000271202607204338853338", "FD1AE10217B84F218F1FF693CABCBFE9", "/"],
        [datetime.datetime(2026, 7, 19, 20, 13, 39), "理财通赎回", "理财通", "活期＋", "/", 500, "零钱通", "已到账", "18000078241026071904007566983029", "180000782426071920001070100035515327384970761515", "/"],
        [datetime.datetime(2026, 7, 10, 10, 0, 0), "拼多多平台商户-退款", "拼多多", "退款", "支出", 23.0, "零钱通", "已全额退款", "45000000000000000000000000000001", "M1", "/"],
        [datetime.datetime(2026, 7, 9, 9, 0, 0), "商户消费", "未知状态商户", "支付请求", "支出", 9.5, "零钱通", "状态不明", "45000000000000000000000000000002", "M2", "/"],
    ]
    path = _build_wechat_xlsx(tmp_path, rows)
    parsed = parse_wechat_xlsx(path)
    assert len(parsed) == 5
    assert [r.status for r in parsed] == [
        RowStatus.SUCCESS,
        RowStatus.SUCCESS,
        RowStatus.SUCCESS,
        RowStatus.REFUND,
        RowStatus.SKIPPED,
    ]


def test_wechat_normalizes_fields(tmp_path):
    rows = [
        [datetime.datetime(2026, 7, 30, 19, 56, 10), "转账", "王琦 (王大可)", "转账备注:微信转账", "支出", 70, "零钱通", "对方已收钱", "53010003298035202607300233568072", "1000050001202607300429226508877", "/"],
        [datetime.datetime(2026, 7, 30, 20, 0, 0), "商户消费", "某店", "商品", "收入", 12.5, "零钱", "已到账", "53010003298035202607300233568073", "M3", "测试备注"],
    ]
    path = _build_wechat_xlsx(tmp_path, rows)
    parsed = parse_wechat_xlsx(path)
    transfer = parsed[0]
    assert transfer.platform == "wechat"
    assert transfer.occurred_at == "2026-07-30 19:56:10"
    assert transfer.amount_cents == 7000
    assert transfer.direction == "expense"
    assert transfer.counterparty == "王琦 (王大可)"
    assert transfer.item_desc == "转账备注:微信转账"
    assert transfer.note == ""
    assert transfer.normalized_hash

    income = parsed[1]
    assert income.direction == "income"
    assert income.amount_cents == 1250
    assert income.note == "测试备注"


def test_wechat_partial_refund_expense_is_original(tmp_path):
    rows = [
        [datetime.datetime(2026, 7, 5, 8, 0, 0), "商户消费", "某店", "商品", "支出", 23.0, "零钱", "已退款(¥23.00)", "TXN-REFUND-PARTIAL", "M9", "/"],
    ]
    path = _build_wechat_xlsx(tmp_path, rows)
    parsed = parse_wechat_xlsx(path)
    assert parsed[0].status == RowStatus.SUCCESS  # 支出方向的部分退款状态属于原消费
    assert parsed[0].status_text == "已退款(¥23.00)"


def test_wechat_missing_header_raises(tmp_path):
    path = tmp_path / "bad.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["nothing"])
    wb.save(path)
    with pytest.raises(ValueError):
        parse_wechat_xlsx(path)
