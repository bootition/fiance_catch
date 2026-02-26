import pytest

from app.logic import parse_amount_to_cents, validate_direction


@pytest.mark.parametrize(
    "s,expected",
    [
        ("0.01", 1),
        ("1", 100),
        ("1.2", 120),
        ("1.20", 120),
        ("10.05", 1005),
    ],
)
def test_parse_amount_to_cents_ok(s, expected):
    assert parse_amount_to_cents(s) == expected


@pytest.mark.parametrize("s", ["", "-1", "abc", "1.234"])
def test_parse_amount_to_cents_bad(s):
    with pytest.raises(ValueError):
        parse_amount_to_cents(s)


@pytest.mark.parametrize("s", ["income", "expense"])
def test_validate_direction_ok(s):
    assert validate_direction(s) == s


@pytest.mark.parametrize("s", ["in", "out", "", "Income"])
def test_validate_direction_bad(s):
    with pytest.raises(ValueError):
        validate_direction(s)
