from app.db import init_db
from app.repo import create_txn, delete_txn, list_txns, update_txn
from app.settings import Settings


def test_create_list_delete(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)

    tid = create_txn(
        settings.db_path,
        date_str="2026-02-25",
        direction="expense",
        amount_cents=1234,
        category="food",
        note="lunch",
    )
    rows = list_txns(settings.db_path, start="2026-02-01", end="2026-02-28")
    assert len(rows) == 1
    assert rows[0]["id"] == tid
    assert rows[0]["amount_cents"] == 1234

    delete_txn(settings.db_path, tid)
    rows2 = list_txns(settings.db_path, start="2026-02-01", end="2026-02-28")
    assert rows2 == []


def test_update_txn_updates_existing_row(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)

    tid = create_txn(
        settings.db_path,
        account_id=999,
        date_str="2026-02-25",
        direction="expense",
        amount_cents=1234,
        category="food",
        note="before-edit",
    )

    updated = update_txn(
        settings.db_path,
        tid,
        account_id=2,
        date_str="2026-02-26",
        direction="income",
        amount_cents=5678,
        category="salary",
        note="after-edit",
    )

    assert updated is True

    rows = list_txns(settings.db_path, start="2026-02-01", end="2026-02-28")
    assert len(rows) == 1
    assert rows[0]["id"] == tid
    assert rows[0]["account_id"] == 1
    assert rows[0]["date"] == "2026-02-26"
    assert rows[0]["direction"] == "income"
    assert rows[0]["amount_cents"] == 5678
    assert rows[0]["category"] == "salary"
    assert rows[0]["note"] == "after-edit"
