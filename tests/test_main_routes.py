import json
import re
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.routers.review as review_router
from app.db import init_db
from app.router_support.settings_access import configure_settings
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


def _extract_review_chart_data(response_text: str, key: str) -> dict:
    match = re.search(rf"const {key} = (.*?);", response_text, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def _review_line_data(response_text: str) -> dict:
    return _extract_review_chart_data(response_text, "lineData")


def _review_pie_data(response_text: str) -> dict:
    return _extract_review_chart_data(response_text, "pieData")


def _dataset_by_key(line_data: dict, dataset_key: str) -> dict:
    for dataset in line_data["datasets"]:
        if dataset.get("datasetKey") == dataset_key:
            return dataset
    raise AssertionError(f"dataset not found: {dataset_key}")


def _review_expense_summary_value(response_text: str) -> float:
    match = re.search(
        r'<div class="metric-card metric-expense">.*?<p class="metric-value">([0-9.\-]+)</p>',
        response_text,
        re.DOTALL,
    )
    assert match is not None
    return float(match.group(1))


@pytest.fixture
def client_and_settings(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)
    monkeypatch.setattr(main, "settings", settings)
    configure_settings(settings)
    with TestClient(main.app) as client:
        yield client, settings


@pytest.fixture
def fixed_review_today(monkeypatch):
    class _FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 3, 31)

    monkeypatch.setattr(review_router, "dt_date", _FixedDate)


def test_index_defaults_to_simplified_chinese_language(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/")
    assert response.status_code == 200
    assert '<html lang="zh-CN">' in response.text
    assert "本地账本" in response.text
    assert "时间范围" in response.text
    assert "清理" in response.text


def test_index_supports_simplified_chinese_language(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/", params={"lang": "zh-CN"})
    assert response.status_code == 200
    assert '<html lang="zh-CN">' in response.text
    assert "本地账本" in response.text
    assert "时间范围" in response.text
    assert "清理" in response.text


def test_invalid_lang_falls_back_to_chinese_without_error(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/", params={"lang": "bad"})
    assert response.status_code == 200
    assert '<html lang="zh-CN">' in response.text
    assert "本地账本" in response.text


def test_review_page_matches_new_surface(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/review")
    assert response.status_code == 200
    assert 'name="account_id"' not in response.text
    assert 'name="show_archived"' not in response.text
    assert "汇总 - 账户" in response.text
    assert "净消费" in response.text
    assert "清理" in response.text
    assert "Import" not in response.text


def test_cleanup_page_is_primary_bulk_delete_surface(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/cleanup")
    assert response.status_code == 200
    assert 'action="/transactions/bulk-delete/preview"' in response.text
    assert 'action="/transactions/bulk-delete/execute"' not in response.text
    assert "批量删除不可恢复。" in response.text
    assert 'href="/import' not in response.text


@pytest.mark.parametrize("path", ["/", "/review", "/cleanup"])
def test_mounted_pages_do_not_emit_dead_lang_or_import_controls(
    client_and_settings, path
):
    client, _ = client_and_settings
    response = client.get(path)

    assert response.status_code == 200
    assert 'name="lang"' not in response.text
    assert "&lang=" not in response.text
    assert 'href="/import' not in response.text


def test_import_routes_are_unmounted_from_product_surface(client_and_settings):
    client, _ = client_and_settings
    assert client.get("/import").status_code == 404
    assert client.post("/import/alipay", data={}).status_code == 404


def test_review_line_chart_includes_income_expense_and_project_datasets(
    client_and_settings, fixed_review_today
):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-29",
        direction="expense",
        amount_cents=1234,
        category="三餐",
        note="sun-expense",
    )

    create_txn(
        settings.db_path,
        date_str="2026-03-30",
        direction="income",
        amount_cents=500,
        category="退款",
        note="sun-income",
    )

    response = client.get("/review", params={"period": "week"})
    assert response.status_code == 200
    line_data = _review_line_data(response.text)
    assert len(line_data["datasets"]) >= 3
    income = _dataset_by_key(line_data, "income_total")
    expense = _dataset_by_key(line_data, "expense_total")
    project = _dataset_by_key(line_data, "project:三餐")
    assert income["label"] == "收入"
    assert expense["label"] == "支出"
    assert project["label"] == "三餐"
    assert income["data"][-1] == 5.0
    assert expense["data"][-1] == 12.34
    assert project["data"][-1] == 12.34
    assert 'data-dataset-key="income_total"' in response.text
    assert 'data-dataset-key="expense_total"' in response.text
    assert 'data-dataset-key="project:三餐"' in response.text


def test_review_line_chart_project_datasets_use_deterministic_top_order(
    client_and_settings, fixed_review_today
):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-29",
        direction="expense",
        amount_cents=2000,
        category="三餐",
        note="meal",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-30",
        direction="expense",
        amount_cents=1200,
        category="交通",
        note="bus",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-31",
        direction="expense",
        amount_cents=1200,
        category="学习",
        note="book",
    )

    response = client.get("/review", params={"period": "week"})
    assert response.status_code == 200
    line_data = _review_line_data(response.text)
    project_keys = [
        dataset["datasetKey"]
        for dataset in line_data["datasets"]
        if dataset["datasetKey"].startswith("project:")
    ]
    assert project_keys[:3] == ["project:三餐", "project:交通", "project:学习"]


def test_review_weekly_buckets_anchor_on_sunday(
    client_and_settings, fixed_review_today
):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-28",
        direction="expense",
        amount_cents=500,
        category="三餐",
        note="sat-expense",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-29",
        direction="expense",
        amount_cents=1000,
        category="三餐",
        note="sun-expense",
    )

    response = client.get("/review", params={"period": "week"})
    assert response.status_code == 200
    line_data = _review_line_data(response.text)
    labels = line_data["labels"]
    series = _dataset_by_key(line_data, "expense_total")["data"]
    assert labels[-2] == "03-22"
    assert labels[-1] == "03-29"
    assert series[-2] == 5.0
    assert series[-1] == 10.0


def test_review_project_mode_only_filters_summary_and_pie(
    client_and_settings, fixed_review_today
):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-29",
        direction="expense",
        amount_cents=1000,
        category="三餐",
        note="meal-a",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-30",
        direction="expense",
        amount_cents=600,
        category="交通",
        note="bus",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-31",
        direction="expense",
        amount_cents=300,
        category="三餐",
        note="meal-b",
    )

    response = client.get(
        "/review",
        params={"period": "week", "project_mode": "only", "project": "三餐"},
    )
    assert response.status_code == 200
    assert _review_expense_summary_value(response.text) == 13.0
    pie_data = _review_pie_data(response.text)
    assert pie_data["labels"] == ["三餐"]
    assert pie_data["values"] == [13.0]


def test_review_project_mode_exclude_filters_summary_and_pie(
    client_and_settings, fixed_review_today
):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-29",
        direction="expense",
        amount_cents=1000,
        category="三餐",
        note="meal-a",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-30",
        direction="expense",
        amount_cents=600,
        category="交通",
        note="bus",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-31",
        direction="expense",
        amount_cents=300,
        category="三餐",
        note="meal-b",
    )

    response = client.get(
        "/review",
        params={"period": "week", "project_mode": "exclude", "project": "三餐"},
    )
    assert response.status_code == 200
    assert _review_expense_summary_value(response.text) == 6.0
    pie_data = _review_pie_data(response.text)
    assert pie_data["labels"] == ["交通"]
    assert pie_data["values"] == [6.0]


def test_review_filtered_pie_matches_filtered_expense_summary(
    client_and_settings, fixed_review_today
):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-29",
        direction="expense",
        amount_cents=900,
        category="三餐",
        note="meal",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-30",
        direction="expense",
        amount_cents=700,
        category="交通",
        note="taxi",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-30",
        direction="income",
        amount_cents=500,
        category="退款",
        note="refund",
    )

    response = client.get(
        "/review",
        params={"period": "week", "project_mode": "exclude", "project": "三餐"},
    )
    assert response.status_code == 200
    expense_summary = _review_expense_summary_value(response.text)
    pie_sum = round(sum(_review_pie_data(response.text)["values"]), 2)
    assert expense_summary == 7.0
    assert pie_sum == expense_summary


def test_review_only_filter_can_return_explicit_empty_expense_result(
    client_and_settings, fixed_review_today
):
    client, settings = client_and_settings
    create_txn(
        settings.db_path,
        date_str="2026-03-30",
        direction="income",
        amount_cents=2500,
        category="三餐",
        note="reimbursement",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-30",
        direction="expense",
        amount_cents=600,
        category="交通",
        note="bus",
    )

    response = client.get(
        "/review",
        params={"period": "week", "project_mode": "only", "project": "三餐"},
    )
    assert response.status_code == 200
    assert _review_expense_summary_value(response.text) == 0.0
    pie_data = _review_pie_data(response.text)
    assert pie_data["labels"] == []
    assert pie_data["values"] == []
    assert "当前窗口暂无支出数据。" in response.text


def test_bulk_delete_preview_and_execute_for_selected_batches(client_and_settings):
    client, settings = client_and_settings
    batch_one = "11111111111111111111111111111111"
    batch_two = "22222222222222222222222222222222"
    create_txn(
        settings.db_path,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=1234,
        category="餐饮",
        note="批次一A",
        import_batch_id=batch_one,
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-11",
        direction="income",
        amount_cents=200,
        category="退款",
        note="批次一B",
        import_batch_id=batch_one,
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-12",
        direction="expense",
        amount_cents=500,
        category="餐饮",
        note="批次二A",
        import_batch_id=batch_two,
    )

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
    assert "已删除: 2" in result_page.text


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


def test_bulk_delete_preview_rejects_reversed_range(client_and_settings):
    client, _ = client_and_settings
    response = client.post(
        "/transactions/bulk-delete/preview",
        data={"start": "2026-03-31", "end": "2026-03-01"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "start must be on or before end"


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


def test_account_management_routes_are_unmounted(client_and_settings):
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
        assert response.json()["detail"] == "Not Found"


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
    assert f"/transactions/{txn_id}" in response.text
    assert 'value="2026-03-10"' in response.text
    assert 'value="12.34"' in response.text
    assert 'value="food"' in response.text
    assert 'value="editable row"' in response.text


def test_update_transaction_htmx_success_refreshes_summary_and_table(
    client_and_settings,
):
    client, settings = client_and_settings
    txn_id = create_txn(
        settings.db_path,
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
        date_str="2026-03-05",
        direction="income",
        amount_cents=500000,
        category="salary",
        note="income",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-06",
        direction="expense",
        amount_cents=1200,
        category="food",
        note="expense",
    )
    create_txn(
        settings.db_path,
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
        date_str="2026-03-05",
        direction="income",
        amount_cents=1000,
        category="salary",
        note="income",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-06",
        direction="expense",
        amount_cents=300,
        category="food",
        note="expense",
    )
    create_txn(
        settings.db_path,
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
