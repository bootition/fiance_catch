import sqlite3

from app.db import init_db
from app.repo import create_txn, delete_txn, get_summary, list_categories, list_txns
from app.settings import Settings


def test_create_txn_always_writes_default_account(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)

    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT OR IGNORE INTO accounts(id, name, archived) VALUES (2, 'Family', 0)"
    )
    conn.commit()
    conn.close()

    txn_id = create_txn(
        settings.db_path,
        account_id=2,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=100,
        category="food",
        note="forced-default",
    )

    conn2 = sqlite3.connect(str(settings.db_path))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT id, account_id FROM transactions WHERE id = ?",
        (txn_id,),
    ).fetchone()
    conn2.close()

    assert row["account_id"] == 1


def test_list_txns_get_summary_and_categories_ignore_account_scope(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)

    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT OR IGNORE INTO accounts(id, name, archived) VALUES (2, 'Family', 0)"
    )
    conn.execute(
        """
        INSERT INTO transactions(account_id, date, direction, amount_cents, category, note)
        VALUES
          (1, '2026-03-10', 'income', 300000, 'salary', 'default-row'),
          (2, '2026-03-11', 'expense', 1200, 'food', 'family-row')
        """
    )
    conn.commit()
    conn.close()

    rows = list_txns(
        settings.db_path,
        account_id=999,
        start="2026-03-01",
        end="2026-03-31",
    )
    assert len(rows) == 2

    summary = get_summary(
        settings.db_path,
        account_id=999,
        start="2026-03-01",
        end="2026-03-31",
    )
    assert summary["income_cents"] == 300000
    assert summary["expense_cents"] == 1200

    categories = list_categories(settings.db_path, account_id=999)
    assert categories == ["food", "salary"]


def test_delete_txn_removes_row_without_account_scope(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)

    txn_id = create_txn(
        settings.db_path,
        account_id=1,
        date_str="2026-03-10",
        direction="expense",
        amount_cents=100,
        category="misc",
        note="to-delete",
    )

    delete_txn(settings.db_path, txn_id, account_id=999)
    rows = list_txns(settings.db_path, start="2026-03-01", end="2026-03-31")
    assert rows == []
