from dataclasses import dataclass


@dataclass(frozen=True)
class Transaction:
    id: int
    date: str
    direction: str
    amount_cents: int
    category: str
    note: str
    created_at: str
    updated_at: str
