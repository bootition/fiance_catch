from app.db import init_db
from app.repo import create_txn, get_summary
from app.settings import Settings


def test_get_summary_totals_and_by_category(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)

    create_txn(
        settings.db_path,
        date_str="2026-02-05",
        direction="income",
        amount_cents=500000,
        category="salary",
        note="monthly salary",
    )
    create_txn(
        settings.db_path,
        date_str="2026-02-06",
        direction="expense",
        amount_cents=1200,
        category="food",
        note="lunch",
    )
    create_txn(
        settings.db_path,
        date_str="2026-02-07",
        direction="expense",
        amount_cents=800,
        category="food",
        note="snack",
    )
    create_txn(
        settings.db_path,
        date_str="2026-02-08",
        direction="expense",
        amount_cents=30000,
        category="rent",
        note="monthly rent",
    )
    create_txn(
        settings.db_path,
        date_str="2026-03-01",
        direction="expense",
        amount_cents=999,
        category="ignore",
        note="outside range",
    )

    summary = get_summary(settings.db_path, start="2026-02-01", end="2026-02-28")

    assert summary["income_cents"] == 500000
    assert summary["expense_cents"] == 32000
    assert summary["by_category"] == [
        {"category": "rent", "amount_cents": 30000},
        {"category": "food", "amount_cents": 2000},
    ]
