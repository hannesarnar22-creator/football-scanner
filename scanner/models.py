"""Pydantic-gagnalíkön fyrir leiki, veðmálasíður, markaði og stuðla.

Öll tímagildi eru tímabeltisvituð og geymd í UTC.
Allir stuðlar og fjárhæðir eru Decimal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SUPPORTED_OU_LINES: frozenset[Decimal] = frozenset({Decimal("1.5"), Decimal("2.5"), Decimal("3.5")})


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Tími verður að hafa tímabelti (UTC)")
    return value.astimezone(UTC)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Gagnauppspretta
# ---------------------------------------------------------------------------

class DataSource(_Frozen):
    """Hvaðan gögnin koma. DEMO-gögn eru aldrei raunverulegir stuðlar."""

    name: str
    kind: Literal["DEMO", "API"]

    @property
    def is_demo(self) -> bool:
        return self.kind == "DEMO"

    @property
    def origin(self) -> DataOrigin:
        return DataOrigin.DEMO if self.is_demo else DataOrigin.API


DEMO_SOURCE = DataSource(name="DEMO-gagnasett", kind="DEMO")


# ---------------------------------------------------------------------------
# Veðmálamörk og veðmálasíða
# ---------------------------------------------------------------------------

class DataOrigin(StrEnum):
    """Uppruni upplýsinga. Raðað frá veikasta (DEMO) til sterkasta (API)."""

    DEMO = "DEMO"
    MANUAL = "Handvirkt"
    API = "Staðfest API"

    @property
    def rank(self) -> int:
        return {DataOrigin.DEMO: 0, DataOrigin.MANUAL: 1, DataOrigin.API: 2}[self]

    @staticmethod
    def weakest(*origins: DataOrigin) -> DataOrigin:
        return min(origins, key=lambda o: o.rank)


class StakeLimits(_Frozen):
    """Veðmálamörk eins valkosts hjá einni síðu. None = UNKNOWN (aldrei áætlað).

    Fjárhæðir eru í gjaldmiðli reikningsins (`currency`).
    """

    source: DataOrigin
    max_stake: Decimal | None = Field(default=None, ge=0)
    min_stake: Decimal | None = Field(default=None, ge=0)
    available_balance: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    stake_step: Decimal | None = Field(default=None, gt=0)
    commission_pct: Decimal | None = Field(default=None, ge=0, lt=100)
    fixed_fee: Decimal | None = Field(default=None, ge=0)
    last_confirmed_at: datetime | None = None

    FIELD_LABELS: ClassVar[dict[str, str]] = {
        "max_stake": "hámarksveðmál",
        "min_stake": "lágmarksveðmál",
        "available_balance": "tiltæk inneign",
        "currency": "gjaldmiðill",
        "stake_step": "veðmálaskref",
        "commission_pct": "þóknun",
        "fixed_fee": "föst gjöld",
        "last_confirmed_at": "tími síðustu staðfestingar",
    }

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("Gjaldmiðill verður að vera ISO-kóði, t.d. ISK eða EUR")
        return v

    @field_validator("last_confirmed_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _ensure_utc(v)

    @model_validator(mode="after")
    def _min_le_max(self) -> StakeLimits:
        if self.min_stake is not None and self.max_stake is not None and self.min_stake > self.max_stake:
            raise ValueError("Lágmarksveðmál má ekki vera hærra en hámarksveðmál")
        return self

    def unknown_fields(self) -> list[str]:
        return [label for name, label in self.FIELD_LABELS.items() if getattr(self, name) is None]


UNKNOWN_LIMITS = StakeLimits(source=DataOrigin.MANUAL)


class Bookmaker(_Frozen):
    """Veðmálasíða og veðmálamörk notandans hjá henni."""

    id: str
    name: str
    limits: StakeLimits = UNKNOWN_LIMITS


# ---------------------------------------------------------------------------
# Uppgjörsreglur og markaðir
# ---------------------------------------------------------------------------

class Period(StrEnum):
    REGULAR_TIME = "90 mín. + uppbótartími"
    FIRST_HALF = "Fyrri hálfleikur"
    INCLUDING_EXTRA_TIME = "Með framlengingu"


class TotalType(StrEnum):
    STANDARD = "Venjulegt"
    ASIAN = "Asian total"


class SettlementRules(_Frozen):
    """Hvernig markaður er gerður upp. Aðeins markaðir með eins reglur mega parast."""

    period: Period = Period.REGULAR_TIME
    total_type: TotalType = TotalType.STANDARD

    @property
    def is_supported(self) -> bool:
        return self.period is Period.REGULAR_TIME and self.total_type is TotalType.STANDARD


class MarketType(StrEnum):
    OVER_UNDER = "Over/Under"
    BTTS = "BTTS"


class Market(_Frozen):
    """Markaður (t.d. Over/Under 2.5). Tveir markaðir eru eins aðeins ef tegund,
    lína og uppgjörsreglur eru allar eins."""

    type: MarketType
    line: Decimal | None = None
    rules: SettlementRules = Field(default_factory=SettlementRules)

    @field_validator("line")
    @classmethod
    def _normalize_line(cls, v: Decimal | None) -> Decimal | None:
        # 2.50 og 2.5 eru sama línan
        return None if v is None else v.normalize()

    @model_validator(mode="after")
    def _check_line(self) -> Market:
        if self.type is MarketType.OVER_UNDER and self.line is None:
            raise ValueError("Over/Under markaður þarf línu")
        if self.type is MarketType.BTTS and self.line is not None:
            raise ValueError("BTTS markaður hefur enga línu")
        return self

    @property
    def is_supported(self) -> bool:
        if not self.rules.is_supported:
            return False
        if self.type is MarketType.OVER_UNDER:
            return self.line in SUPPORTED_OU_LINES
        return True

    def unsupported_reason(self) -> str | None:
        if self.rules.period is not Period.REGULAR_TIME:
            return f"Tímabil ekki stutt: {self.rules.period}"
        if self.rules.total_type is not TotalType.STANDARD:
            return f"Tegund ekki studd: {self.rules.total_type}"
        if self.type is MarketType.OVER_UNDER and self.line not in SUPPORTED_OU_LINES:
            return f"Lína ekki studd: {self.line}"
        return None

    @property
    def label(self) -> str:
        if self.type is MarketType.BTTS:
            base = "BTTS (Yes/No)"
        else:
            line = f"{self.line:f}"
            base = f"Over/Under {line}"
        if not self.rules.is_supported:
            base += f" [{self.rules.period}, {self.rules.total_type}]"
        return base


def over_under(line: str | Decimal, **rules) -> Market:
    return Market(type=MarketType.OVER_UNDER, line=Decimal(str(line)), rules=SettlementRules(**rules))


def btts(**rules) -> Market:
    return Market(type=MarketType.BTTS, rules=SettlementRules(**rules))


# ---------------------------------------------------------------------------
# Valkostir
# ---------------------------------------------------------------------------

class Outcome(StrEnum):
    OVER = "Over"
    UNDER = "Under"
    YES = "Yes"
    NO = "No"

    @property
    def opposite(self) -> Outcome:
        return {
            Outcome.OVER: Outcome.UNDER,
            Outcome.UNDER: Outcome.OVER,
            Outcome.YES: Outcome.NO,
            Outcome.NO: Outcome.YES,
        }[self]

    @property
    def market_type(self) -> MarketType:
        return MarketType.OVER_UNDER if self in (Outcome.OVER, Outcome.UNDER) else MarketType.BTTS


OUTCOME_PAIRS: dict[MarketType, tuple[Outcome, Outcome]] = {
    MarketType.OVER_UNDER: (Outcome.OVER, Outcome.UNDER),
    MarketType.BTTS: (Outcome.YES, Outcome.NO),
}


# ---------------------------------------------------------------------------
# Tímastimpill og stuðull
# ---------------------------------------------------------------------------

class Timestamp(_Frozen):
    """Hvenær stuðull var sóttur og (ef vitað) hvenær veðmálasíðan uppfærði hann."""

    captured_at: datetime
    bookmaker_updated_at: datetime | None = None

    @field_validator("captured_at", "bookmaker_updated_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _ensure_utc(v)

    @property
    def effective(self) -> datetime:
        """Elsti þekkti tími – varfærið mat á aldri stuðulsins."""
        if self.bookmaker_updated_at is None:
            return self.captured_at
        return min(self.captured_at, self.bookmaker_updated_at)

    def age(self, now: datetime) -> timedelta:
        return _ensure_utc(now) - self.effective


class OddsQuote(_Frozen):
    """Einn stuðull hjá einni veðmálasíðu fyrir einn valkost."""

    bookmaker_id: str
    market: Market
    outcome: Outcome
    price: Decimal = Field(gt=Decimal(1))
    timestamp: Timestamp
    source: DataSource

    @model_validator(mode="after")
    def _check_outcome(self) -> OddsQuote:
        if self.outcome.market_type is not self.market.type:
            raise ValueError(f"Valkostur {self.outcome} passar ekki við markað {self.market.type}")
        return self


# ---------------------------------------------------------------------------
# Leikir
# ---------------------------------------------------------------------------

class BookmakerEvent(_Frozen):
    """Leikur eins og hann birtist hjá einni veðmálasíðu (óstaðlað heiti)."""

    bookmaker_id: str
    event_id: str
    competition: str
    home_team: str
    away_team: str
    home_code: str | None = None
    away_code: str | None = None
    kickoff_utc: datetime
    quotes: tuple[OddsQuote, ...] = ()
    source: DataSource

    @field_validator("kickoff_utc")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class Match(_Frozen):
    """Staðlaður fótboltaleikur eftir samræmingu."""

    match_id: str
    competition_id: str
    competition_name: str
    home_team_id: str
    home_team_name: str
    away_team_id: str
    away_team_name: str
    kickoff_utc: datetime

    @field_validator("kickoff_utc")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)

    @property
    def label(self) -> str:
        return f"{self.home_team_name} – {self.away_team_name}"
