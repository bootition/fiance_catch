from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def validate_direction(s: str) -> str:
    if s not in {"income", "expense"}:
        raise ValueError("direction must be income or expense")
    return s


def parse_amount_to_cents(s: str) -> int:
    if not isinstance(s, str) or not s.strip():
        raise ValueError("amount required")
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError("amount invalid") from e
    if d < 0:
        raise ValueError("amount must be non-negative")
    cents = (d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if (d * 100) != cents:
        raise ValueError("amount supports up to 2 decimals")
    return int(cents)
