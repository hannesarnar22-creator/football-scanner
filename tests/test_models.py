from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from scanner.models import (
    DEMO_SOURCE,
    Market,
    MarketType,
    OddsQuote,
    Outcome,
    Period,
    Timestamp,
    TotalType,
    btts,
    over_under,
)

T = Timestamp(captured_at=datetime(2026, 11, 1, 12, tzinfo=timezone.utc))


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        Timestamp(captured_at=datetime(2026, 11, 1, 12))


def test_timestamps_converted_to_utc():
    t = Timestamp(captured_at=datetime(2026, 11, 1, 14, tzinfo=timezone(timedelta(hours=2))))
    assert t.captured_at == datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    assert t.captured_at.utcoffset() == timedelta(0)


def test_effective_timestamp_is_oldest():
    t = Timestamp(
        captured_at=datetime(2026, 11, 1, 12, tzinfo=timezone.utc),
        bookmaker_updated_at=datetime(2026, 11, 1, 11, tzinfo=timezone.utc),
    )
    assert t.effective.hour == 11


def test_lines_normalized():
    assert over_under("2.50") == over_under("2.5")
    assert hash(over_under("2.50")) == hash(over_under("2.5"))


def test_markets_with_different_rules_differ():
    assert over_under("2.5") != over_under("2.5", period=Period.FIRST_HALF)
    assert over_under("2.5") != over_under("2.5", total_type=TotalType.ASIAN)
    assert over_under("2.5") != over_under("3.5")


@pytest.mark.parametrize("line", ["1.5", "2.5", "3.5"])
def test_supported_lines(line):
    assert over_under(line).is_supported


@pytest.mark.parametrize(
    "market",
    [
        over_under("0.5"),
        over_under("4.5"),
        over_under("2.25", total_type=TotalType.ASIAN),
        over_under("2.5", period=Period.FIRST_HALF),
        over_under("2.5", period=Period.INCLUDING_EXTRA_TIME),
        btts(period=Period.FIRST_HALF),
    ],
)
def test_unsupported_markets(market):
    assert not market.is_supported
    assert market.unsupported_reason()


def test_market_line_rules():
    with pytest.raises(ValidationError):
        Market(type=MarketType.OVER_UNDER)
    with pytest.raises(ValidationError):
        Market(type=MarketType.BTTS, line=Decimal("2.5"))


def test_outcome_must_match_market():
    with pytest.raises(ValidationError):
        OddsQuote(bookmaker_id="x", market=btts(), outcome=Outcome.OVER,
                  price=Decimal("2"), timestamp=T, source=DEMO_SOURCE)


@pytest.mark.parametrize("price", ["1.00", "0.5", "-2"])
def test_price_must_exceed_one(price):
    with pytest.raises(ValidationError):
        OddsQuote(bookmaker_id="x", market=btts(), outcome=Outcome.YES,
                  price=Decimal(price), timestamp=T, source=DEMO_SOURCE)


def test_outcome_opposites():
    assert Outcome.OVER.opposite is Outcome.UNDER
    assert Outcome.YES.opposite is Outcome.NO


def test_models_are_immutable():
    m = over_under("2.5")
    with pytest.raises(ValidationError):
        m.line = Decimal("3.5")


def test_demo_source_flag():
    assert DEMO_SOURCE.is_demo
