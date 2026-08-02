import datetime

import openpyxl
import pytest

from app.db import init_db
from app.importing.alipay import parse_alipay_csv_bytes
from app.importing.service import import_file
from app.ledger_repo import get_import_batch, list_import_batches, list_source_transactions
from app.settings import Settings

ALIPAY_HEADER = (
    "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,"
    "收/付款方式,交易状态,交易订单号,商家订单号,备注"
)


@pytest.fixture
def db(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "ledger.sqlite")
    init_db(settings)
    return settings.db_path


def _alipay_file(tmp_path, name="alipay.csv", rows=None):
    lines = [
        "----------------导出信息----------------",
        ALIPAY_HEADER,
    ]
    if rows is None:
        rows = [
            "2026-07-31 19:21:36,日用百货,信美佳超市,/,消费,支出,25.00,余额宝,交易成功,TXN-A1,,",
            "2026-07-31 18:00:00,转账红包,鸿,188******65,7月份闲鱼收入,收入,27.38,账户余额,交易成功,TXN-A2,,",
            "2026-07-30 17:12:03,日用百货,拼多多平台商户,pdd***@yiran.com,退款-商户单号X,不计收支,40.00,余额宝,退款成功,TXN-A3_RM123,,",
            "2026-07-28 01:17:06,日用百货,拼多多平台商户,pdd***@yiran.com,商户单号X,支出,40.00,余额宝,交易关闭,TXN-A4,,",
            "2026-07-27 12:00:00,投资理财,余额宝,yue***@csfunds.com.cn,余额宝-单次转入,不计收支,86.73,账户余额,交易成功,TXN-A5,,",
        ]
    lines += rows
    path = tmp_path / name
    path.write_bytes("\n".join(lines).encode("gb18030"))
    return path


def test_import_alipay_creates_batch_and_counts(db, tmp_path):
    path = _alipay_file(tmp_path)
    result = import_file(db, path, "alipay")
    assert result.total == 5
    assert result.added == 4  # 3 success + 1 refund
    assert result.skipped == 1  # 关闭
    assert result.refunds == 1
    assert result.duplicates == 0
    assert result.file_name == "alipay.csv"
    assert result.file_fingerprint

    batch = get_import_batch(db, result.batch_id)
    assert batch["platform"] == "alipay"
    assert batch["file_fingerprint"] == result.file_fingerprint
    assert int(batch["row_count"]) == 5
    assert int(batch["accepted_count"]) == 4
    assert int(batch["skipped_count"]) == 1


def test_import_duplicate_file_deduped(db, tmp_path):
    path = _alipay_file(tmp_path)
    first = import_file(db, path, "alipay")
    second = import_file(db, path, "alipay")
    assert second.batch_id != first.batch_id
    assert second.added == 0
    assert second.duplicates == 4  # 4 已存在；关闭行不入库不算重复
    assert second.skipped == 1
    sources = list_source_transactions(db, platform="alipay")
    assert len(sources) == 4
    assert len(list_import_batches(db)) == 2


def test_import_wechat_xlsx(db, tmp_path):
    path = tmp_path / "wechat.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in [
        ["微信支付账单明细"],
        ["------------------微信支付账单明细列表------------------"],
        ["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)", "支付方式", "当前状态", "交易单号", "商户单号", "备注"],
        [datetime.datetime(2026, 7, 30, 19, 56, 10), "转账", "王琦", "转账备注:微信转账", "支出", 70, "零钱通", "对方已收钱", "WX-1", "M1", "/"],
        [datetime.datetime(2026, 7, 20, 14, 14, 29), "商户消费", "圆明园", "支付请求", "支出", 60, "零钱通", "支付成功", "WX-2", "M2", "/"],
        [datetime.datetime(2026, 7, 10, 10, 0, 0), "拼多多平台商户-退款", "拼多多", "退款", "支出", 23.0, "零钱通", "已全额退款", "WX-3", "M3", "/"],
        [datetime.datetime(2026, 7, 9, 9, 0, 0), "商户消费", "未知", "支付请求", "支出", 9.5, "零钱通", "状态不明", "WX-4", "M4", "/"],
    ]:
        ws.append(row)
    wb.save(path)

    result = import_file(db, path, "wechat")
    assert result.total == 4
    assert result.added == 3
    assert result.refunds == 1
    assert result.skipped == 1
    sources = list_source_transactions(db, platform="wechat")
    assert len(sources) == 3
    assert all(s["platform"] == "wechat" for s in sources)


def test_import_unsupported_platform_raises(db, tmp_path):
    path = _alipay_file(tmp_path)
    with pytest.raises(ValueError):
        import_file(db, path, "unknown")


def test_import_missing_file_raises(db, tmp_path):
    with pytest.raises(FileNotFoundError):
        import_file(db, tmp_path / "missing.csv", "alipay")


def test_refund_row_persists_status_text(db, tmp_path):
    path = _alipay_file(tmp_path)
    import_file(db, path, "alipay")
    sources = list_source_transactions(db, platform="alipay")
    refund = next(s for s in sources if s["source_txn_id"].startswith("TXN-A3"))
    assert refund["status_text"] == "退款成功"
    assert refund["direction"] == "neutral"


def test_import_is_atomic_midway_failure_rolls_back(db, tmp_path, monkeypatch):
    import app.importing.service as service

    path = _alipay_file(tmp_path)
    original = service._insert_source_transaction
    calls = {"n": 0}

    def failing(conn, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated disk failure")
        return original(conn, **kwargs)

    monkeypatch.setattr(service, "_insert_source_transaction", failing)
    with pytest.raises(RuntimeError):
        import_file(db, path, "alipay")

    assert list_import_batches(db) == []
    assert list_source_transactions(db) == []


def test_import_is_atomic_count_update_failure_rolls_back(db, tmp_path, monkeypatch):
    import app.importing.service as service

    path = _alipay_file(tmp_path)

    def failing_counts(conn, batch_id, **kwargs):
        raise RuntimeError("simulated count update failure")

    monkeypatch.setattr(service, "_update_batch_counts", failing_counts)
    with pytest.raises(RuntimeError):
        import_file(db, path, "alipay")

    assert list_import_batches(db) == []
    assert list_source_transactions(db) == []


def test_import_retry_after_failure_succeeds(db, tmp_path, monkeypatch):
    import app.importing.service as service

    path = _alipay_file(tmp_path)
    original = service._insert_source_transaction
    calls = {"n": 0}

    def failing_once(conn, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure")
        return original(conn, **kwargs)

    monkeypatch.setattr(service, "_insert_source_transaction", failing_once)
    with pytest.raises(RuntimeError):
        import_file(db, path, "alipay")
    monkeypatch.undo()

    retry = import_file(db, path, "alipay")
    assert retry.added == 4  # 3 success + 1 refund，全部新增
    assert retry.duplicates == 0
    assert retry.skipped == 1
    assert len(list_source_transactions(db, platform="alipay")) == 4
    assert len(list_import_batches(db)) == 1


def test_import_empty_source_txn_id_rejected(db, tmp_path):
    rows = [
        "2026-07-31 19:21:36,日用百货,某商户,/,消费,支出,25.00,余额宝,交易成功,TXN-OK-1,,",
        "2026-07-31 18:00:00,日用百货,空单号商户,/,消费,支出,15.00,余额宝,交易成功,,,",
        "2026-07-31 17:00:00,日用百货,另一商户,/,消费,支出,5.00,余额宝,交易成功,TXN-OK-2,,",
    ]
    path = _alipay_file(tmp_path, name="empty_id.csv", rows=rows)
    result = import_file(db, path, "alipay")
    assert result.added == 2
    assert result.invalid == 1
    sources = list_source_transactions(db, platform="alipay")
    assert len(sources) == 2
    assert all(s["source_txn_id"] for s in sources)


def test_import_zero_amount_success_row_preserved(db, tmp_path):
    rows = [
        "2026-07-31 19:21:36,其他,某商户,/,0元活动,支出,0.00,余额宝,交易成功,TXN-ZERO-1,,",
        "2026-07-31 18:00:00,其他,另一商户,/,正常消费,支出,10.00,余额宝,交易成功,TXN-ZERO-2,,",
    ]
    path = _alipay_file(tmp_path, name="zero_amount.csv", rows=rows)
    result = import_file(db, path, "alipay")
    assert result.added == 2
    assert result.invalid == 0
    sources = list_source_transactions(db, platform="alipay")
    zero = next(s for s in sources if s["source_txn_id"] == "TXN-ZERO-1")
    assert int(zero["amount_cents"]) == 0
