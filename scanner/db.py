"""SQLite-gagnagrunnur (SQLAlchemy 2).

Hönnunarreglur:
  - DEMO og LIVE eru aðskilin með `mode` í hverri gagnatöflu. Allar fyrirspurnir
    krefjast `mode` – það er ekkert sjálfgefið gildi sem gæti blandað þeim saman.
  - Fjárhæðir og stuðlar eru geymd sem texti (DecimalText) svo Decimal haldist nákvæmt.
  - Allir tímar eru geymdir í UTC (UTCDateTime).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_URL = f"sqlite:///{(PROJECT_ROOT / 'data' / 'scanner.sqlite').as_posix()}"


class DataMode(StrEnum):
    DEMO = "DEMO"
    LIVE = "LIVE"


# ---------------------------------------------------------------------------
# Sérsniðnar dálktegundir
# ---------------------------------------------------------------------------

class DecimalText(TypeDecorator):
    """Decimal geymt sem texti – engin float-nálgun í SQLite."""

    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError(f"Búist við Decimal, fékk {type(value).__name__}")
        return str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class UTCDateTime(TypeDecorator):
    """Tímabeltisvitaður tími, geymdur sem UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Tími verður að hafa tímabelti")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Töflur
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class CompetitionRow(Base):
    __tablename__ = "competitions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))


class BookmakerRow(Base):
    __tablename__ = "bookmakers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))


class MarketRow(Base):
    """Markaður + lína + uppgjörsreglur. `line` er '' fyrir BTTS."""

    __tablename__ = "markets"
    __table_args__ = (UniqueConstraint("type", "line", "period", "total_type"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    line: Mapped[str] = mapped_column(String(16))
    period: Mapped[str] = mapped_column(String(32))
    total_type: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(64))


class MatchRow(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("mode", "match_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    match_key: Mapped[str] = mapped_column(String(128))
    competition_id: Mapped[str] = mapped_column(ForeignKey("competitions.id"))
    home_team_id: Mapped[str] = mapped_column(String(32))
    home_team_name: Mapped[str] = mapped_column(String(128))
    away_team_id: Mapped[str] = mapped_column(String(32))
    away_team_name: Mapped[str] = mapped_column(String(128))
    kickoff_utc: Mapped[datetime] = mapped_column(UTCDateTime)

    competition: Mapped[CompetitionRow] = relationship()

    @property
    def label(self) -> str:
        return f"{self.home_team_name} – {self.away_team_name}"


class OddsSnapshotRow(Base):
    """Ein útgáfa af stuðli. Ný lína er aðeins skráð þegar stuðullinn breytist."""

    __tablename__ = "odds_snapshots"
    __table_args__ = (Index("ix_snapshot_series", "mode", "match_id", "market_id", "outcome", "bookmaker_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8))
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    outcome: Mapped[str] = mapped_column(String(8))
    bookmaker_id: Mapped[str] = mapped_column(ForeignKey("bookmakers.id"))
    price: Mapped[Decimal] = mapped_column(DecimalText)
    source_time: Mapped[datetime] = mapped_column(UTCDateTime)  # upprunatími gagna
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime)  # skráningartími (UTC)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)  # síðast séð óbreytt

    match: Mapped[MatchRow] = relationship()
    market: Mapped[MarketRow] = relationship()


class OpportunityRow(Base):
    """Eitt einstakt arbitrage-tækifæri (stöðugt auðkenni) með nýjustu gildum."""

    __tablename__ = "opportunities"
    __table_args__ = (UniqueConstraint("mode", "opp_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    opp_key: Mapped[str] = mapped_column(String(64))
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    outcome_a: Mapped[str] = mapped_column(String(8))
    outcome_b: Mapped[str] = mapped_column(String(8))
    bookmaker_a_id: Mapped[str] = mapped_column(ForeignKey("bookmakers.id"))
    bookmaker_b_id: Mapped[str] = mapped_column(ForeignKey("bookmakers.id"))

    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Nýjustu gildi
    odds_a: Mapped[Decimal] = mapped_column(DecimalText)
    odds_b: Mapped[Decimal] = mapped_column(DecimalText)
    theoretical_margin_pct: Mapped[Decimal] = mapped_column(DecimalText)
    real_margin_pct: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    worst_case_profit: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    total_stake: Mapped[Decimal] = mapped_column(DecimalText)
    max_feasible_total: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    status: Mapped[str] = mapped_column(String(64))
    data_basis: Mapped[str] = mapped_column(String(32))
    max_stake_a: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    max_stake_b: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    balance_a: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    balance_b: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    currency_a: Mapped[str | None] = mapped_column(String(3), nullable=True)
    currency_b: Mapped[str | None] = mapped_column(String(3), nullable=True)
    peak_margin_pct: Mapped[Decimal] = mapped_column(DecimalText)

    match: Mapped[MatchRow] = relationship()
    market: Mapped[MarketRow] = relationship()
    bookmaker_a: Mapped[BookmakerRow] = relationship(foreign_keys=[bookmaker_a_id])
    bookmaker_b: Mapped[BookmakerRow] = relationship(foreign_keys=[bookmaker_b_id])
    observations: Mapped[list[OpportunityObservationRow]] = relationship(
        back_populates="opportunity", order_by="OpportunityObservationRow.observed_at"
    )


class OpportunityObservationRow(Base):
    """Saga tækifæris. Ný lína aðeins þegar stuðlar eða mat breytast."""

    __tablename__ = "opportunity_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    odds_a: Mapped[Decimal] = mapped_column(DecimalText)
    odds_b: Mapped[Decimal] = mapped_column(DecimalText)
    source_time_a: Mapped[datetime] = mapped_column(UTCDateTime)
    source_time_b: Mapped[datetime] = mapped_column(UTCDateTime)
    theoretical_margin_pct: Mapped[Decimal] = mapped_column(DecimalText)
    real_margin_pct: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    worst_case_profit: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    total_stake: Mapped[Decimal] = mapped_column(DecimalText)
    status: Mapped[str] = mapped_column(String(64))

    opportunity: Mapped[OpportunityRow] = relationship(back_populates="observations")


class AlertRow(Base):
    """Viðvörun um tækifæri. Í mesta lagi EIN viðvörun á hvert tækifæri (mode + opp_key)."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("mode", "opp_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    opp_key: Mapped[str] = mapped_column(String(64))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    threshold_pct: Mapped[Decimal] = mapped_column(DecimalText)
    match_label: Mapped[str] = mapped_column(String(256))
    competition: Mapped[str] = mapped_column(String(128))
    market_label: Mapped[str] = mapped_column(String(64))
    bookmaker_a: Mapped[str] = mapped_column(String(128))
    bookmaker_b: Mapped[str] = mapped_column(String(128))
    outcome_a: Mapped[str] = mapped_column(String(8))
    outcome_b: Mapped[str] = mapped_column(String(8))
    odds_a: Mapped[Decimal] = mapped_column(DecimalText)
    odds_b: Mapped[Decimal] = mapped_column(DecimalText)
    margin_pct: Mapped[Decimal] = mapped_column(DecimalText)
    worst_case_profit: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    total_stake: Mapped[Decimal] = mapped_column(DecimalText)
    status: Mapped[str] = mapped_column(String(64))
    odds_age_seconds: Mapped[int] = mapped_column(Integer)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)

    opportunity: Mapped[OpportunityRow] = relationship()


# ---------------------------------------------------------------------------
# Tenging
# ---------------------------------------------------------------------------

@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Database:
    def __init__(self, url: str = DEFAULT_DATABASE_URL) -> None:
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.engine = create_engine(url)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._sessions()

    def dispose(self) -> None:
        self.engine.dispose()
