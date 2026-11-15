"""Íslensk talnasnið: punktur sem þúsundaskil, komma sem tugabrotskil."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal


def _to_decimal(value: Decimal | int | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(repr(value) if isinstance(value, float) else value)


def _icelandic_number(value: Decimal | int | float, decimals: int) -> str:
    quantum = Decimal(1).scaleb(-decimals)
    q = _to_decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)
    if q == 0:
        q = abs(q)  # forðast "-0"
    text = f"{q:,.{decimals}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def format_number(value: Decimal | int | float, decimals: int = 2) -> str:
    """2.1 -> '2,10'"""
    return _icelandic_number(value, decimals)


def format_isk(amount: Decimal | int | float, decimals: int = 0) -> str:
    """100000 -> '100.000 kr.', 1234.5 (decimals=2) -> '1.234,50 kr.'"""
    return f"{_icelandic_number(amount, decimals)} kr."


def format_money(amount: Decimal | int | float, currency: str | None) -> str:
    """ISK í heilum krónum, aðrir gjaldmiðlar með tveimur aukastöfum."""
    if currency in (None, "ISK"):
        q = _to_decimal(amount)
        return format_isk(q, 0 if q == q.to_integral_value() else 2)
    return f"{_icelandic_number(amount, 2)} {currency}"


def format_percent(value: Decimal | int | float, decimals: int = 2) -> str:
    """2.345 -> '2,35%'"""
    return f"{_icelandic_number(value, decimals)}%"


def format_timestamp(ts: datetime) -> str:
    """Dagsetning og tími á íslensku sniði, t.d. '01.11.2026 14:05'."""
    return ts.strftime("%d.%m.%Y %H:%M")
