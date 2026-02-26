from app.db import init_db
from app.repo import (
    create_account,
    create_txn,
    delete_txn,
    get_summary,
    list_accounts,
    list_txns,
)
from app.settings import Settings


def test_create_and_list_accounts(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)

    accounts = list_accounts(settings.db_path)
    assert [account["name"] for account in accounts] == ["Default"]

    new_account_id = create_account(settings.db_path, "Family")
    assert new_account_id > 1

    accounts2 = list_accounts(settings.db_path)
    assert [account["name"] for account in accounts2] == ["Default", "Family"]


def test_transactions_are_scoped_by_account(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)

    family_id = create_account(settings.db_path, "Family")

    default_txn_id = create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="income",
        amount_cents=100000,
        category="salary",
        note="default account",
    )
    family_txn_id = create_txn(
        settings.db_path,
        account_id=family_id,
        date_str="2026-03-11",
        direction="expense",
        amount_cents=2500,
        category="food",
        note="family account",
    )

    default_rows = list_txns(
        settings.db_path,
        account_id=1,
        start="2026-03-01",
        end="2026-03-31",
    )
    family_rows = list_txns(
        settings.db_path,
        account_id=family_id,
        start="2026-03-01",
        end="2026-03-31",
    )
    assert [row["id"] for row in default_rows] == [default_txn_id]
    assert [row["id"] for row in family_rows] == [family_txn_id]

    delete_txn(settings.db_path, family_txn_id, account_id=1)
    family_rows_after_wrong_delete = list_txns(
        settings.db_path,
        account_id=family_id,
        start="2026-03-01",
        end="2026-03-31",
    )
    assert [row["id"] for row in family_rows_after_wrong_delete] == [family_txn_id]

    delete_txn(settings.db_path, family_txn_id, account_id=family_id)
    family_rows_after_delete = list_txns(
        settings.db_path,
        account_id=family_id,
        start="2026-03-01",
        end="2026-03-31",
    )
    assert family_rows_after_delete == []

    default_summary = get_summary(
        settings.db_path,
        account_id=1,
        start="2026-03-01",
        end="2026-03-31",
    )
    family_summary = get_summary(
        settings.db_path,
        account_id=family_id,
        start="2026-03-01",
        end="2026-03-31",
    )
    assert default_summary["income_cents"] == 100000
    assert default_summary["expense_cents"] == 0
    assert family_summary["income_cents"] == 0
    assert family_summary["expense_cents"] == 0
