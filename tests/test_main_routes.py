import json
import re
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import init_db
from app.repo import create_txn, list_txns
from app.settings import Settings


def _txn_form(**overrides):
    data = {
        "date": "2026-03-10",
        "direction": "expense",
        "amount": "12.34",
        "category": "food",
        "note": "lunch",
        "start": "2026-03-01",
        "end": "2026-03-31",
    }
    data.update(overrides)
    return data


def _review_line_labels(response_text: str) -> list[str]:
    match = re.search(r"const lineData = (.*?);", response_text, re.DOTALL)
    assert match is not None
    line_data = json.loads(match.group(1))
    return [str(dataset["label"]) for dataset in line_data["datasets"]]


def _review_line_data(response_text: str) -> dict:
    match = re.search(r"const lineData = (.*?);", response_text, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def _preview_session_id_from_location(location: str) -> str:
    match = re.search(r"/import/preview/([0-9a-f]{32})", location)
    assert match is not None
    return match.group(1)


def _list_import_rows(db_path, session_id: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT *
            FROM import_rows
            WHERE session_id = ?
            ORDER BY row_no ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()


def _import_preview_counts(db_path, session_id: str) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN parse_status = 'valid' AND deleted = 0 THEN 1 ELSE 0 END), 0) AS valid_count,
              COALESCE(SUM(CASE WHEN parse_status = 'skipped_status' AND deleted = 0 THEN 1 ELSE 0 END), 0) AS skipped_status_count,
              COALESCE(SUM(CASE WHEN parse_status = 'invalid' AND deleted = 0 THEN 1 ELSE 0 END), 0) AS invalid_count,
              COALESCE(SUM(CASE WHEN deleted = 1 THEN 1 ELSE 0 END), 0) AS deleted_count,
              COALESCE(SUM(CASE WHEN selected = 1 AND deleted = 0 THEN 1 ELSE 0 END), 0) AS selected_count
            FROM import_rows
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    return {
        "valid": int(row["valid_count"]),
        "skipped_status": int(row["skipped_status_count"]),
        "invalid": int(row["invalid_count"]),
        "deleted": int(row["deleted_count"]),
        "selected": int(row["selected_count"]),
    }


@pytest.fixture
def client_and_settings(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)
    monkeypatch.setattr(main, "settings", settings)
    with TestClient(main.app) as client:
        yield client, settings


def test_index_defaults_to_english_language(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/")
    assert response.status_code == 200
    assert '<html lang="en">' in response.text
    assert "Local Ledger" in response.text
    assert "Date Range" in response.text


def test_index_supports_simplified_chinese_language(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/", params={"lang": "zh-CN"})
    assert response.status_code == 200
    assert '<html lang="zh-CN">' in response.text
    assert "本地账本" in response.text
    assert "时间范围" in response.text


def test_invalid_lang_falls_back_to_english_without_error(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/", params={"lang": "bad"})
    assert response.status_code == 200
    assert '<html lang="en">' in response.text
    assert "Local Ledger" in response.text


def test_review_page_uses_single_ledger_ui(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/review")
    assert response.status_code == 200
    assert 'name="account_id"' not in response.text
    assert 'name="show_archived"' not in response.text
    assert "Summary - Account" in response.text
    assert "Net consumption" in response.text
    assert "reserved" not in response.text.lower()


def test_review_page_line_chart_english_current_window_only(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/review", params={"lang": "en"})
    assert response.status_code == 200
    assert "Current Income" in response.text
    assert "Current Expense" in response.text
    assert "Previous Income" not in response.text
    assert "Previous Expense" not in response.text

    line_data = _review_line_data(response.text)
    labels = _review_line_labels(response.text)
    assert "Current Income" in labels
    assert "Current Expense" in labels
    assert "Previous Income" not in labels
    assert "Previous Expense" not in labels
    assert len(line_data["datasets"]) == 2
    assert "previous window" not in response.text.lower()


def test_review_page_line_chart_simplified_chinese_current_window_only(
    client_and_settings,
):
    client, _ = client_and_settings
    response = client.get("/review", params={"lang": "zh-CN"})
    assert response.status_code == 200

    line_data = _review_line_data(response.text)
    labels = _review_line_labels(response.text)
    assert "当前收入" in labels
    assert "当前支出" in labels
    assert "上一窗口收入" not in labels
    assert "上一窗口支出" not in labels
    assert len(line_data["datasets"]) == 2
    assert "上一窗口" not in response.text


def test_import_page_uses_single_ledger_ui(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/import")
    assert response.status_code == 200
    assert 'action="/import/alipay/preview"' in response.text
    assert 'name="file"' in response.text
    assert "重复跳过" not in response.text
    assert "去重依据" not in response.text


def test_import_preview_upload_does_not_write_transactions(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐,ALP001,交易成功\n"
    )

    response = client.post(
        "/import/alipay/preview",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={"file": ("alipay-preview.csv", csv_body.encode("utf-8"), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    session_id = _preview_session_id_from_location(response.headers["location"])

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows == []

    preview_rows = _list_import_rows(settings.db_path, session_id)
    assert len(preview_rows) == 1
    assert str(preview_rows[0]["parse_status"]) == "valid"


def test_import_preview_edit_then_commit_persists_edited_category(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐,ALP001,交易成功\n"
    )

    preview_response = client.post(
        "/import/alipay/preview",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={
            "file": ("alipay-preview-edit.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert preview_response.status_code == 303
    session_id = _preview_session_id_from_location(preview_response.headers["location"])

    preview_rows = _list_import_rows(settings.db_path, session_id)
    row_id = int(preview_rows[0]["id"])

    update_response = client.post(
        f"/import/preview/{session_id}/row/{row_id}",
        data={
            "category": "food-fixed",
            "note": "edited-note",
            "selected": "1",
            "start": "2026-03-01",
            "end": "2026-03-31",
            "lang": "en",
        },
        follow_redirects=False,
    )
    assert update_response.status_code == 303

    commit_response = client.post(
        f"/import/preview/{session_id}/commit",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        follow_redirects=False,
    )
    assert commit_response.status_code == 303
    assert "imported=1" in commit_response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["category"] == "food-fixed"
    assert rows[0]["note"] == "edited-note"


def test_import_preview_bulk_delete_excludes_deleted_rows_on_commit(
    client_and_settings,
):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐A,ALP001,交易成功\n"
        "2026-03-11 10:20:00,支出,8.88,餐饮美食,午餐B,ALP002,交易成功\n"
    )

    preview_response = client.post(
        "/import/alipay/preview",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={
            "file": ("alipay-preview-delete.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert preview_response.status_code == 303
    session_id = _preview_session_id_from_location(preview_response.headers["location"])

    bulk_delete_response = client.post(
        f"/import/preview/{session_id}/bulk-delete",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        follow_redirects=False,
    )
    assert bulk_delete_response.status_code == 303

    commit_response = client.post(
        f"/import/preview/{session_id}/commit",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        follow_redirects=False,
    )
    assert commit_response.status_code == 303
    assert "imported=0" in commit_response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows == []


def test_import_preview_bulk_set_category_applies_selected_only(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐A,ALP001,交易成功\n"
        "2026-03-11 10:20:00,支出,8.88,交通出行,地铁,ALP002,交易成功\n"
    )

    preview_response = client.post(
        "/import/alipay/preview",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={
            "file": (
                "alipay-preview-bulk-category.csv",
                csv_body.encode("utf-8"),
                "text/csv",
            )
        },
        follow_redirects=False,
    )
    assert preview_response.status_code == 303
    session_id = _preview_session_id_from_location(preview_response.headers["location"])

    preview_rows = _list_import_rows(settings.db_path, session_id)
    second_row_id = int(preview_rows[1]["id"])
    deselect_response = client.post(
        f"/import/preview/{session_id}/row/{second_row_id}",
        data={
            "category": str(preview_rows[1]["category"]),
            "note": str(preview_rows[1]["note"]),
            "start": "2026-03-01",
            "end": "2026-03-31",
            "lang": "zh-CN",
        },
        follow_redirects=False,
    )
    assert deselect_response.status_code == 303

    bulk_update_response = client.post(
        f"/import/preview/{session_id}/bulk-update",
        data={
            "action": "set_category",
            "target_category": "fixed-category",
            "start": "2026-03-01",
            "end": "2026-03-31",
            "lang": "zh-CN",
        },
        follow_redirects=False,
    )
    assert bulk_update_response.status_code == 303

    updated_rows = _list_import_rows(settings.db_path, session_id)
    assert str(updated_rows[0]["category"]) == "fixed-category"
    assert str(updated_rows[1]["category"]) == "交通出行"


def test_import_preview_create_rule_applies_to_next_import(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,投资理财,基金,ALP001,交易成功\n"
    )

    first_preview = client.post(
        "/import/alipay/preview",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={
            "file": ("alipay-preview-rule-1.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert first_preview.status_code == 303
    first_session_id = _preview_session_id_from_location(
        first_preview.headers["location"]
    )

    create_rule_response = client.post(
        f"/import/preview/{first_session_id}/bulk-update",
        data={
            "action": "create_rule",
            "target_category": "investment",
            "start": "2026-03-01",
            "end": "2026-03-31",
            "lang": "en",
        },
        follow_redirects=False,
    )
    assert create_rule_response.status_code == 303

    second_preview = client.post(
        "/import/alipay/preview",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        files={
            "file": ("alipay-preview-rule-2.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert second_preview.status_code == 303
    second_session_id = _preview_session_id_from_location(
        second_preview.headers["location"]
    )

    second_rows = _list_import_rows(settings.db_path, second_session_id)
    assert len(second_rows) == 1
    assert str(second_rows[0]["category"]) == "investment"


def test_import_preview_discarded_session_cannot_commit(client_and_settings):
    client, _ = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐,ALP001,交易成功\n"
    )

    preview_response = client.post(
        "/import/alipay/preview",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        files={
            "file": ("alipay-preview-discard.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert preview_response.status_code == 303
    session_id = _preview_session_id_from_location(preview_response.headers["location"])

    discard_response = client.post(
        f"/import/preview/{session_id}/discard",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        follow_redirects=False,
    )
    assert discard_response.status_code == 303

    commit_response = client.post(
        f"/import/preview/{session_id}/commit",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        follow_redirects=False,
    )
    assert commit_response.status_code == 400


def test_import_preview_counts_match_final_commit_result(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐,ALP001,交易成功\n"
        "2026-03-11 10:20:00,支出,8.88,交通出行,地铁,ALP002,交易关闭\n"
        "2026-03-12 11:20:00,支出,abc,交通出行,无效金额,ALP003,交易成功\n"
    )

    preview_response = client.post(
        "/import/alipay/preview",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={
            "file": ("alipay-preview-counts.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert preview_response.status_code == 303
    session_id = _preview_session_id_from_location(preview_response.headers["location"])

    counts = _import_preview_counts(settings.db_path, session_id)
    assert counts == {
        "valid": 1,
        "skipped_status": 1,
        "invalid": 1,
        "deleted": 0,
        "selected": 1,
    }

    commit_response = client.post(
        f"/import/preview/{session_id}/commit",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        follow_redirects=False,
    )
    assert commit_response.status_code == 303
    assert "imported=1" in commit_response.headers["location"]
    assert "deleted=0" in commit_response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1


def test_import_alipay_csv_imports_every_row_without_trade_no_dedup(
    client_and_settings,
):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐,ALP001,交易成功\n"
        "2026-03-11 10:20:00,收入,2.00,退款,退款到账,ALP002,交易成功\n"
    )

    first = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        files={"file": ("alipay.csv", csv_body.encode("utf-8"), "text/csv")},
        follow_redirects=False,
    )
    assert first.status_code == 303
    assert "imported=2" in first.headers["location"]
    assert "skipped_status=0" in first.headers["location"]
    assert "skipped_non_cashflow=0" in first.headers["location"]
    assert "invalid=0" in first.headers["location"]
    assert "duplicate=" not in first.headers["location"]

    second = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        files={"file": ("alipay.csv", csv_body.encode("utf-8"), "text/csv")},
        follow_redirects=False,
    )
    assert second.status_code == 303
    assert "imported=2" in second.headers["location"]
    assert "skipped_status=0" in second.headers["location"]
    assert "skipped_non_cashflow=0" in second.headers["location"]
    assert "invalid=0" in second.headers["location"]
    assert "duplicate=" not in second.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 4
    assert [row["note"] for row in rows].count("午餐") == 2
    assert [row["note"] for row in rows].count("退款到账") == 2


def test_import_alipay_csv_same_trade_no_rows_are_both_imported(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐A,SAME001,交易成功\n"
        "2026-03-10 09:05:00,支出,9.99,餐饮美食,午餐B,SAME001,交易成功\n"
    )

    response = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        files={
            "file": ("alipay-same-trade-no.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "imported=2" in response.headers["location"]
    assert "skipped_status=0" in response.headers["location"]
    assert "skipped_non_cashflow=0" in response.headers["location"]
    assert "invalid=0" in response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 2
    assert {row["note"] for row in rows} == {"午餐A", "午餐B"}


def test_import_alipay_status_yi_shou_ru_is_importable(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,收入,20.00,转账红包,收款,INCOME001,已收入\n"
    )

    response = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={
            "file": ("alipay-status-income.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "imported=1" in response.headers["location"]
    assert "skipped_status=0" in response.headers["location"]
    assert "skipped_non_cashflow=0" in response.headers["location"]
    assert "invalid=0" in response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["note"] == "收款"


def test_import_alipay_status_jiao_yi_guan_bi_counted_as_skipped(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐,CLOSED001,交易关闭\n"
    )

    response = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={
            "file": ("alipay-status-closed.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "imported=0" in response.headers["location"]
    assert "skipped_status=1" in response.headers["location"]
    assert "skipped_non_cashflow=0" in response.headers["location"]
    assert "invalid=0" in response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows == []


def test_import_alipay_include_neutral_toggle_affects_skipped_non_cashflow(
    client_and_settings,
):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,支出成功,A001,交易成功\n"
        "2026-03-10 09:02:00,不计收支,8.00,投资理财,中性成功,A002,交易成功\n"
        "2026-03-10 09:03:00,支出,5.00,餐饮美食,关闭跳过,A003,交易关闭\n"
    )

    include_response = client.post(
        "/import/alipay",
        data={
            "start": "2026-03-01",
            "end": "2026-03-31",
            "lang": "zh-CN",
            "include_neutral": "1",
        },
        files={
            "file": ("alipay-neutral-include.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert include_response.status_code == 303
    assert "imported=2" in include_response.headers["location"]
    assert "skipped_status=1" in include_response.headers["location"]
    assert "skipped_non_cashflow=0" in include_response.headers["location"]
    assert "invalid=0" in include_response.headers["location"]

    rows_after_include = list_txns(
        settings.db_path,
        start="2026-03-01",
        end="2026-03-31",
    )
    assert len(rows_after_include) == 2
    assert {row["direction"] for row in rows_after_include} == {"expense", "neutral"}

    skip_response = client.post(
        "/import/alipay",
        data={
            "start": "2026-03-01",
            "end": "2026-03-31",
            "lang": "zh-CN",
            "include_neutral": "0",
        },
        files={
            "file": ("alipay-neutral-skip.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    assert skip_response.status_code == 303
    assert "imported=1" in skip_response.headers["location"]
    assert "skipped_status=1" in skip_response.headers["location"]
    assert "skipped_non_cashflow=1" in skip_response.headers["location"]
    assert "invalid=0" in skip_response.headers["location"]


def test_import_alipay_csv_with_preface_header_and_tail_note_lines(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "------------------------------------------------------------------------------------\n"
        "导出信息：\n"
        "姓名：测试\n"
        "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注,\n"
        "2026-03-10 09:01:00,餐饮美食,早餐店,test@example.com,早餐,支出,12.34,余额宝,交易成功,ALP-REAL-001,M001,,\n"
        "\n"
        "特别提示：本行是说明，不是交易\n"
    )

    response = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={"file": ("alipay-real-style.csv", csv_body.encode("utf-8"), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "imported=1" in response.headers["location"]
    assert "skipped_status=0" in response.headers["location"]
    assert "skipped_non_cashflow=0" in response.headers["location"]
    assert "invalid=0" in response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["note"] == "早餐"


def test_import_alipay_csv_without_trade_no_still_imports(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,午餐,交易成功\n"
    )

    response = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={
            "file": (
                "alipay-missing-trade-no.csv",
                csv_body.encode("utf-8"),
                "text/csv",
            )
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "imported=1" in response.headers["location"]
    assert "skipped_status=0" in response.headers["location"]
    assert "skipped_non_cashflow=0" in response.headers["location"]
    assert "invalid=0" in response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["note"] == "午餐"


def test_import_alipay_csv_supports_slash_date_format(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易状态\n"
        "2026/3/6 09:12:00,支出,9.99,餐饮美食,日期容错,交易成功\n"
    )

    response = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={"file": ("alipay-date-flex.csv", csv_body.encode("utf-8"), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "imported=1" in response.headers["location"]
    assert "invalid=0" in response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-03-06"


def test_import_alipay_generates_batch_id_and_rows_are_tagged(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,批次A,BATCH001,交易成功\n"
        "2026-03-11 10:20:00,收入,2.00,退款,批次B,BATCH002,交易成功\n"
    )

    response = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "zh-CN"},
        files={"file": ("alipay-batch.csv", csv_body.encode("utf-8"), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    batch_id = query["batch_id"][0]
    assert len(batch_id) == 32

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 2
    assert {row["import_batch_id"] for row in rows} == {batch_id}


def test_delete_import_batch_removes_only_target_batch(client_and_settings):
    client, settings = client_and_settings
    keep_id = create_txn(
        settings.db_path,
        date_str="2026-03-09",
        direction="expense",
        amount_cents=500,
        category="food",
        note="keep-row",
    )
    assert keep_id > 0

    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,删A,BATCHD01,交易成功\n"
        "2026-03-11 10:20:00,收入,2.00,退款,删B,BATCHD02,交易成功\n"
    )
    import_response = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        files={
            "file": ("alipay-delete-batch.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    query = parse_qs(urlparse(import_response.headers["location"]).query)
    batch_id = query["batch_id"][0]

    delete_response = client.post(
        f"/import/batches/{batch_id}/delete",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        follow_redirects=False,
    )
    assert delete_response.status_code == 303
    assert "deleted=2" in delete_response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["note"] == "keep-row"
    assert rows[0]["import_batch_id"] is None


def test_delete_import_batch_rejects_invalid_batch_id(client_and_settings):
    client, _ = client_and_settings
    response = client.post(
        "/import/batches/not-a-batch/delete",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid batch_id"


def test_delete_import_batch_can_be_called_twice_without_crash(client_and_settings):
    client, settings = client_and_settings
    csv_body = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,二次删,BATCHR01,交易成功\n"
    )
    import_response = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        files={
            "file": ("alipay-repeat-delete.csv", csv_body.encode("utf-8"), "text/csv")
        },
        follow_redirects=False,
    )
    batch_id = parse_qs(urlparse(import_response.headers["location"]).query)[
        "batch_id"
    ][0]

    first_delete = client.post(
        f"/import/batches/{batch_id}/delete",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        follow_redirects=False,
    )
    assert first_delete.status_code == 303
    assert "deleted=1" in first_delete.headers["location"]

    second_delete = client.post(
        f"/import/batches/{batch_id}/delete",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        follow_redirects=False,
    )
    assert second_delete.status_code == 303
    assert "deleted=0" in second_delete.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows == []


def test_bulk_delete_preview_and_execute_for_selected_batches(client_and_settings):
    client, settings = client_and_settings

    csv_batch_one = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-10 09:01:00,支出,12.34,餐饮美食,批次一A,BULK-A1,交易成功\n"
        "2026-03-11 10:20:00,收入,2.00,退款,批次一B,BULK-A2,交易成功\n"
    )
    csv_batch_two = (
        "交易创建时间,收/支,金额（元）,交易分类,商品说明,交易号,交易状态\n"
        "2026-03-12 10:20:00,支出,5.00,餐饮美食,批次二A,BULK-B1,交易成功\n"
    )

    import_one = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        files={"file": ("bulk-a.csv", csv_batch_one.encode("utf-8"), "text/csv")},
        follow_redirects=False,
    )
    batch_one = parse_qs(urlparse(import_one.headers["location"]).query)["batch_id"][0]

    import_two = client.post(
        "/import/alipay",
        data={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        files={"file": ("bulk-b.csv", csv_batch_two.encode("utf-8"), "text/csv")},
        follow_redirects=False,
    )
    batch_two = parse_qs(urlparse(import_two.headers["location"]).query)["batch_id"][0]

    preview = client.post(
        "/transactions/bulk-delete/preview",
        data={"batch_ids": [batch_one]},
    )
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["matched_count"] == 2
    assert preview_payload["delete_token"]

    execute = client.post(
        "/transactions/bulk-delete/execute",
        data={
            "delete_token": preview_payload["delete_token"],
            "confirm_text": "DELETE",
            "expected_count": "2",
            "page_start": "2026-03-01",
            "page_end": "2026-03-31",
            "lang": "en",
        },
        follow_redirects=False,
    )
    assert execute.status_code == 303
    assert "deleted=2" in execute.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["import_batch_id"] == batch_two

    result_page = client.get(execute.headers["location"])
    assert result_page.status_code == 200
    assert "Deleted: 2" in result_page.text


def test_bulk_delete_by_conditions_only_removes_matching_rows(client_and_settings):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=1234,
        category="food",
        note="keyword lunch",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-11",
        direction="expense",
        amount_cents=800,
        category="transport",
        note="keyword bus",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-12",
        direction="income",
        amount_cents=2000,
        category="refund",
        note="keyword refund",
    )

    preview = client.post(
        "/transactions/bulk-delete/preview",
        data={
            "start": "2026-03-01",
            "end": "2026-03-31",
            "direction": "expense",
            "category": "food",
            "note_contains": "lunch",
            "imported_only": "0",
        },
    )
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["matched_count"] == 1

    execute = client.post(
        "/transactions/bulk-delete/execute",
        data={
            "delete_token": payload["delete_token"],
            "confirm_text": "DELETE",
            "expected_count": "1",
            "page_start": "2026-03-01",
            "page_end": "2026-03-31",
            "lang": "en",
        },
        follow_redirects=False,
    )
    assert execute.status_code == 303
    assert "deleted=1" in execute.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    notes = {row["note"] for row in rows}
    assert notes == {"keyword bus", "keyword refund"}


def test_bulk_delete_execute_rejects_count_mismatch_with_409(client_and_settings):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=100,
        category="food",
        note="mismatch-1",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-11",
        direction="expense",
        amount_cents=200,
        category="food",
        note="mismatch-2",
    )

    preview = client.post(
        "/transactions/bulk-delete/preview",
        data={"direction": "expense"},
    )
    token = preview.json()["delete_token"]
    assert preview.json()["matched_count"] == 2

    create_txn(
        settings.db_path,
        date_str="2026-03-12",
        direction="expense",
        amount_cents=300,
        category="food",
        note="mismatch-3",
    )

    execute = client.post(
        "/transactions/bulk-delete/execute",
        data={
            "delete_token": token,
            "confirm_text": "DELETE",
            "expected_count": "2",
        },
    )
    assert execute.status_code == 409


def test_bulk_delete_execute_rejects_wrong_confirm_text(client_and_settings):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=100,
        category="food",
        note="confirm-check",
    )

    preview = client.post(
        "/transactions/bulk-delete/preview",
        data={"direction": "expense"},
    )
    token = preview.json()["delete_token"]

    execute = client.post(
        "/transactions/bulk-delete/execute",
        data={
            "delete_token": token,
            "confirm_text": "WRONG",
            "expected_count": str(preview.json()["matched_count"]),
        },
    )
    assert execute.status_code == 400
    assert execute.json()["detail"] == "confirm_text must be DELETE"


def test_bulk_delete_rejects_delete_all_without_explicit_flag(client_and_settings):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=100,
        category="food",
        note="all-delete-1",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-11",
        direction="income",
        amount_cents=300,
        category="salary",
        note="all-delete-2",
    )

    rejected_preview = client.post("/transactions/bulk-delete/preview", data={})
    assert rejected_preview.status_code == 400
    assert rejected_preview.json()["detail"] == "empty delete conditions not allowed"

    allowed_preview = client.post(
        "/transactions/bulk-delete/preview",
        data={"allow_delete_all": "1"},
    )
    assert allowed_preview.status_code == 200
    payload = allowed_preview.json()
    assert payload["matched_count"] == 2

    wrong_confirm = client.post(
        "/transactions/bulk-delete/execute",
        data={
            "delete_token": payload["delete_token"],
            "confirm_text": "DELETE",
            "expected_count": "2",
            "allow_delete_all": "1",
        },
    )
    assert wrong_confirm.status_code == 400
    assert wrong_confirm.json()["detail"] == "confirm_text must be DELETE ALL"

    execute = client.post(
        "/transactions/bulk-delete/execute",
        data={
            "delete_token": payload["delete_token"],
            "confirm_text": "DELETE ALL",
            "expected_count": "2",
            "allow_delete_all": "1",
            "page_start": "2026-03-01",
            "page_end": "2026-03-31",
            "lang": "en",
        },
        follow_redirects=False,
    )
    assert execute.status_code == 303
    assert "deleted=2" in execute.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows == []


def test_account_management_routes_are_disabled(client_and_settings):
    client, _ = client_and_settings
    paths = [
        "/accounts",
        "/accounts/1/rename",
        "/accounts/1/archive",
        "/accounts/1/restore",
        "/accounts/1/delete",
    ]
    for path in paths:
        response = client.post(path, data={})
        assert response.status_code == 404
        assert response.json()["detail"] == "account management disabled"


def test_create_transaction_success(client_and_settings):
    client, settings = client_and_settings
    response = client.post(
        "/transactions",
        data=_txn_form(note="single ledger"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "start=2026-03-01" in response.headers["location"]
    assert "end=2026-03-31" in response.headers["location"]

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["account_id"] == 1
    assert rows[0]["note"] == "single ledger"


def test_create_transaction_htmx_success(client_and_settings):
    client, settings = client_and_settings
    response = client.post(
        "/transactions",
        data=_txn_form(note="htmx path"),
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "htmx path" in response.text

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["note"] == "htmx path"


def test_delete_transaction_success(client_and_settings):
    client, settings = client_and_settings
    txn_id = create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=100,
        category="misc",
        note="to delete",
    )

    response = client.post(
        f"/transactions/{txn_id}/delete",
        data={"start": "2026-03-01", "end": "2026-03-31"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows == []


def test_edit_transaction_form_renders_existing_values(client_and_settings):
    client, settings = client_and_settings
    txn_id = create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=1234,
        category="food",
        note="editable row",
    )

    response = client.get(
        f"/transactions/{txn_id}/edit",
        params={"start": "2026-03-01", "end": "2026-03-31", "lang": "en"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert f'/transactions/{txn_id}' in response.text
    assert 'value="2026-03-10"' in response.text
    assert 'value="12.34"' in response.text
    assert 'value="food"' in response.text
    assert 'value="editable row"' in response.text


def test_update_transaction_htmx_success_refreshes_summary_and_table(client_and_settings):
    client, settings = client_and_settings
    txn_id = create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=1234,
        category="food",
        note="before update",
    )

    response = client.post(
        f"/transactions/{txn_id}",
        data={
            "date": "2026-03-11",
            "direction": "income",
            "amount": "88.00",
            "category": "salary",
            "note": "after update",
            "start": "2026-03-01",
            "end": "2026-03-31",
            "lang": "en",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "after update" in response.text
    assert "88.00" in response.text
    assert "before update" not in response.text

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert len(rows) == 1
    assert rows[0]["id"] == txn_id
    assert rows[0]["date"] == "2026-03-11"
    assert rows[0]["direction"] == "income"
    assert rows[0]["amount_cents"] == 8800
    assert rows[0]["category"] == "salary"
    assert rows[0]["note"] == "after update"


def test_update_transaction_rejects_invalid_date(client_and_settings):
    client, settings = client_and_settings
    txn_id = create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=1234,
        category="food",
        note="bad-edit-target",
    )

    response = client.post(
        f"/transactions/{txn_id}",
        data={
            "date": "2026/03/11",
            "direction": "expense",
            "amount": "8.80",
            "category": "food",
            "note": "still bad",
            "start": "2026-03-01",
            "end": "2026-03-31",
            "lang": "en",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "date must be YYYY-MM-DD"

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows[0]["date"] == "2026-03-10"
    assert rows[0]["note"] == "bad-edit-target"


def test_update_transaction_blank_note_defaults_to_wu(client_and_settings):
    client, settings = client_and_settings
    txn_id = create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=1234,
        category="food",
        note="will-clear-note",
    )

    response = client.post(
        f"/transactions/{txn_id}",
        data={
            "date": "2026-03-10",
            "direction": "expense",
            "amount": "12.34",
            "category": "food",
            "note": "   ",
            "start": "2026-03-01",
            "end": "2026-03-31",
            "lang": "en",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows[0]["note"] == "无"


def test_create_transaction_rejects_invalid_date(client_and_settings):
    client, _ = client_and_settings
    response = client.post("/transactions", data=_txn_form(date="2026/03/10"))
    assert response.status_code == 400
    assert response.json()["detail"] == "date must be YYYY-MM-DD"


@pytest.mark.parametrize(
    "path,params,expected_detail",
    [
        (
            "/",
            {"start": "2026-3-01", "end": "2026-03-31"},
            "start must be YYYY-MM-DD",
        ),
        (
            "/export.csv",
            {"start": "2026-03-01", "end": "2026-02-30"},
            "end must be YYYY-MM-DD",
        ),
    ],
)
def test_invalid_range_returns_400(client_and_settings, path, params, expected_detail):
    client, _ = client_and_settings
    response = client.get(path, params=params)
    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_summary_is_correct_in_single_ledger_mode(client_and_settings):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-05",
        direction="income",
        amount_cents=500000,
        category="salary",
        note="income",
    )
    create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-06",
        direction="expense",
        amount_cents=1200,
        category="food",
        note="expense",
    )
    create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-04-01",
        direction="expense",
        amount_cents=999,
        category="ignore",
        note="outside",
    )

    response = client.get(
        "/",
        params={"start": "2026-03-01", "end": "2026-03-31"},
    )
    assert response.status_code == 200
    assert "5000.00" in response.text
    assert "12.00" in response.text
    assert "outside" not in response.text


def test_summary_ignores_neutral_transactions(client_and_settings):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-05",
        direction="income",
        amount_cents=1000,
        category="salary",
        note="income",
    )
    create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-06",
        direction="expense",
        amount_cents=300,
        category="food",
        note="expense",
    )
    create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-07",
        direction="neutral",
        amount_cents=9900,
        category="transfer",
        note="neutral",
    )

    response = client.get(
        "/",
        params={"start": "2026-03-01", "end": "2026-03-31"},
    )
    assert response.status_code == 200
    assert "10.00" in response.text
    assert "3.00" in response.text


def test_export_csv_works_without_account_id(client_and_settings):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=1200,
        category="food",
        note="default-only",
    )

    response = client.get(
        "/export.csv",
        params={"start": "2026-03-01", "end": "2026-03-31"},
    )
    assert response.status_code == 200
    assert (
        response.headers["Content-Disposition"]
        == 'attachment; filename="ledger-2026-03-01-to-2026-03-31.csv"'
    )
    assert "default-only" in response.text


def test_export_csv_ignores_account_id_if_provided(client_and_settings):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=1200,
        category="food",
        note="ignore-account-param",
    )

    response = client.get(
        "/export.csv",
        params={
            "account_id": 999,
            "start": "2026-03-01",
            "end": "2026-03-31",
        },
    )
    assert response.status_code == 200
    assert "ignore-account-param" in response.text


def test_legacy_multi_account_rows_are_visible_in_single_ledger_mode(
    client_and_settings,
):
    client, settings = client_and_settings
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT OR IGNORE INTO accounts(id, name, archived) VALUES (2, 'Family', 0)"
    )
    conn.execute(
        """
        INSERT INTO transactions(account_id, date, direction, amount_cents, category, note)
        VALUES
          (1, '2026-03-10', 'expense', 100, 'misc', 'default-row'),
          (2, '2026-03-11', 'expense', 200, 'misc', 'family-row')
        """
    )
    conn.commit()
    conn.close()

    response = client.get(
        "/",
        params={"start": "2026-03-01", "end": "2026-03-31"},
    )
    assert response.status_code == 200
    assert "default-row" in response.text
    assert "family-row" in response.text
    assert "3.00" in response.text


def test_category_datalist_allows_suggest_and_custom_values(client_and_settings):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=100,
        category="medical",
        note="seed",
    )

    response = client.get(
        "/",
        params={"start": "2026-03-01", "end": "2026-03-31"},
    )
    assert response.status_code == 200
    assert 'datalist id="category-options"' in response.text
    assert '<option value="medical"></option>' in response.text
    assert '<option value="food"></option>' in response.text

    custom_response = client.post(
        "/transactions",
        data=_txn_form(category="pet-care", note="pet"),
        follow_redirects=False,
    )
    assert custom_response.status_code == 303
    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert any(row["category"] == "pet-care" for row in rows)


def test_note_empty_or_missing_defaults_to_wu(client_and_settings):
    client, settings = client_and_settings

    blank_response = client.post(
        "/transactions",
        data=_txn_form(note="   "),
        follow_redirects=False,
    )
    assert blank_response.status_code == 303

    missing_note_data = _txn_form()
    missing_note_data.pop("note")
    missing_response = client.post(
        "/transactions",
        data=missing_note_data,
        follow_redirects=False,
    )
    assert missing_response.status_code == 303

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    notes = [row["note"] for row in rows]
    assert notes.count("无") >= 2


def test_note_non_empty_is_trimmed(client_and_settings):
    client, settings = client_and_settings
    response = client.post(
        "/transactions",
        data=_txn_form(note="  trimmed note  "),
        follow_redirects=False,
    )
    assert response.status_code == 303

    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows[0]["note"] == "trimmed note"
