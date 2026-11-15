from datetime import datetime

from scanner.formatting import format_isk, format_number, format_percent, format_timestamp


def test_format_isk():
    assert format_isk(100_000) == "100.000 kr."
    assert format_isk(1_234_567.6) == "1.234.568 kr."
    assert format_isk(0) == "0 kr."


def test_format_decimal_values():
    from decimal import Decimal

    assert format_isk(Decimal("3734.10"), 2) == "3.734,10 kr."
    assert format_isk(Decimal("-5000"), 2) == "-5.000,00 kr."
    assert format_isk(Decimal("-0.004"), 2) == "0,00 kr."
    assert format_number(Decimal("2.1")) == "2,10"
    assert format_percent(Decimal("3.73494")) == "3,73%"


def test_format_percent():
    assert format_percent(1.84) == "1,84%"
    assert format_percent(2.345, decimals=1) == "2,3%"


def test_format_timestamp():
    assert format_timestamp(datetime(2026, 11, 1, 14, 5)) == "01.11.2026 14:05"
