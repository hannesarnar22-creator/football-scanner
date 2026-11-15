"""Hjálparföll til að smíða prófunargögn í fáum línum."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

from scanner.models import (
    Bookmaker,
    BookmakerEvent,
    DataOrigin,
    DataSource,
    Market,
    OddsQuote,
    Outcome,
    StakeLimits,
    Timestamp,
)
from scanner.scan import ScanReport, scan_events

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
KICKOFF = NOW + timedelta(hours=5)
TEST_SOURCE = DataSource(name="Prófun", kind="API")


def limits(**overrides) -> StakeLimits:
    """Öll mörk þekkt, rúm og nýstaðfest. Yfirskrifa með **overrides."""
    values = dict(
        source=DataOrigin.MANUAL,
        currency="ISK",
        stake_step=Decimal(1),
        min_stake=Decimal(100),
        max_stake=Decimal(1_000_000),
        available_balance=Decimal(1_000_000),
        commission_pct=Decimal(0),
        fixed_fee=Decimal(0),
        last_confirmed_at=NOW - timedelta(minutes=5),
    )
    values.update(overrides)
    return StakeLimits(**values)


def _known(bm_id: str, name: str, **overrides) -> Bookmaker:
    return Bookmaker(id=bm_id, name=name, limits=limits(**overrides))


BOOKS = {
    "coolbet": _known("coolbet", "Coolbet"),
    "bet365": _known("bet365", "bet365"),
    "epicbet": _known("epicbet", "Epicbet"),
}

_ids = count()


def quote(bm: str, market: Market, outcome: Outcome, price: str, age: int = 30,
          updated_age: int | None = None, source: DataSource = TEST_SOURCE, now: datetime = NOW) -> OddsQuote:
    return OddsQuote(
        bookmaker_id=bm,
        market=market,
        outcome=outcome,
        price=Decimal(price),
        timestamp=Timestamp(
            captured_at=now - timedelta(seconds=age),
            bookmaker_updated_at=None if updated_age is None else now - timedelta(seconds=updated_age),
        ),
        source=source,
    )


def event(bm: str, home: str, away: str, *quotes: tuple, competition: str = "Premier League",
          kickoff: datetime = KICKOFF, event_id: str | None = None,
          home_code: str | None = None, away_code: str | None = None,
          source: DataSource = TEST_SOURCE, now: datetime = NOW) -> BookmakerEvent:
    """quotes: (market, outcome, price) eða (market, outcome, price, age)"""
    return BookmakerEvent(
        bookmaker_id=bm,
        event_id=event_id or f"{bm}-{next(_ids)}",
        competition=competition,
        home_team=home,
        away_team=away,
        home_code=home_code,
        away_code=away_code,
        kickoff_utc=kickoff,
        source=source,
        quotes=tuple(quote(bm, *q, source=source, now=now) for q in quotes),
    )


def run(*events: BookmakerEvent, books: dict[str, Bookmaker] | None = None, now: datetime = NOW,
        **kw) -> ScanReport:
    return scan_events(list(events), books or BOOKS, now, **kw)
