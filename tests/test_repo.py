from app.db import init_db
from app.repo import create_txn, delete_txn, list_txns
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
