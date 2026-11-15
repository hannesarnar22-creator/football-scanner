"""Rökfræði mælaborðsins: síur, röðun og yfirlitstölur (án Streamlit).

Yfirlitstölur eru alltaf reiknaðar úr SÖMU síuðu línum og taflan sýnir,
svo þær geti aldrei stangast á.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from scanner.models import Match
from scanner.risk import RiskAssessment, RiskStatus, assess_risk
from scanner.scan import Opportunity, ScanReport

HIGH_MARGIN_PCT = Decimal(5)

MARGIN_PRESETS: dict[str, Decimal | None] = {
    "0%": Decimal(0),
    "2%": Decimal(2),
    "3%": Decimal(3),
    "5%": Decimal(5),
    "Sérsniðið": None,
}
KICKOFF_WINDOWS: dict[str, timedelta | None] = {
    "Allir leikir": None,
    "Næstu 6 klst.": timedelta(hours=6),
    "Næstu 24 klst.": timedelta(hours=24),
    "Næstu 48 klst.": timedelta(hours=48),
    "Næstu 7 dagar": timedelta(days=7),
}
ODDS_AGE_LIMITS: dict[str, timedelta | None] = {
    "Engin mörk": None,
    "1 mín.": timedelta(minutes=1),
    "2 mín.": timedelta(minutes=2),
    "5 mín.": timedelta(minutes=5),
}


@dataclass(frozen=True)
class DashboardFilters:
    """Tómt mengi = allt leyft."""

    min_margin_pct: Decimal = Decimal(0)
    competitions: frozenset[str] = frozenset()
    markets: frozenset[str] = frozenset()
    bookmakers: frozenset[str] = frozenset()  # BÁÐAR síður tækifæris verða að vera í menginu
    kickoff_within: timedelta | None = None
    max_odds_age: timedelta | None = None
    statuses: frozenset[RiskStatus] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Row:
    """Tækifæri ásamt áhættumati (reiknað einu sinni)."""

    opportunity: Opportunity
    risk: RiskAssessment

    @property
    def key(self) -> str:
        return self.opportunity.key

    @property
    def margin_pct(self) -> Decimal:
        return self.opportunity.margin_pct

    @property
    def worst_case_profit(self) -> Decimal | None:
        return self.opportunity.feasibility.worst_case_profit


@dataclass(frozen=True)
class Kpis:
    match_count: int
    arbitrage_count: int
    high_margin_count: int  # ≥ 5%
    best_margin_pct: Decimal | None
    best_profit: Decimal | None
    best_profit_row: Row | None
    last_update: datetime | None


def sort_rows(rows: list[Row]) -> list[Row]:
    """Hæsta framlegð fyrst; jafnt -> hærri hagnaður; síðan stöðugt eftir auðkenni."""
    return sorted(
        rows,
        key=lambda r: (-r.margin_pct, -(r.worst_case_profit if r.worst_case_profit is not None else Decimal("-1e18")),
                       r.key),
    )


def build_rows(report: ScanReport, now: datetime) -> list[Row]:
    """Aðeins arbitrage-tækifæri (S < 1), hvert með áhættumati, röðuð."""
    return sort_rows([Row(o, assess_risk(o, now)) for o in report.arbitrage])


def match_in_scope(m: Match, f: DashboardFilters, now: datetime) -> bool:
    if f.competitions and m.competition_name not in f.competitions:
        return False
    if f.kickoff_within is not None and not (now < m.kickoff_utc <= now + f.kickoff_within):
        return False
    return True


def row_passes(r: Row, f: DashboardFilters, now: datetime) -> bool:
    o = r.opportunity
    if r.margin_pct < f.min_margin_pct:
        return False
    if not match_in_scope(o.match, f, now):
        return False
    if f.markets and o.market.label not in f.markets:
        return False
    if f.bookmakers and not {o.bookmaker_a.name, o.bookmaker_b.name} <= f.bookmakers:
        return False
    if f.max_odds_age is not None and r.risk.odds_age > f.max_odds_age:
        return False
    if f.statuses and r.risk.status not in f.statuses:
        return False
    return True


def apply_filters(rows: list[Row], f: DashboardFilters, now: datetime) -> list[Row]:
    return sort_rows([r for r in rows if row_passes(r, f, now)])


def high_margin_rows(rows: list[Row], f: DashboardFilters, now: datetime,
                     threshold: Decimal = HIGH_MARGIN_PCT) -> list[Row]:
    """5%+ tækifæri: allar síur gilda, en lágmarksframlegð er að minnsta kosti `threshold`."""
    stricter = DashboardFilters(**{**f.__dict__, "min_margin_pct": max(f.min_margin_pct, threshold)})
    return apply_filters(rows, stricter, now)


UNCONFIRMED_PROFIT_NOTE = "áætlun – ekki staðfestur hagnaður"


def profit_note(r: Row) -> str | None:
    """Aðeins 'Uppfyllir skráð skilyrði' má sýna hagnað án fyrirvara."""
    return None if r.risk.status is RiskStatus.MEETS else UNCONFIRMED_PROFIT_NOTE


def profit_source_text(r: Row) -> str:
    o = r.opportunity
    note = profit_note(r)
    return (f"Hæsti áætlaði nettóhagnaður: {o.match.label} · {o.market.label} · {r.risk.status.label}"
            + (f" – **{note}**" if note else ""))


def compute_kpis(report: ScanReport, filtered: list[Row], f: DashboardFilters, now: datetime,
                 last_update: datetime | None) -> Kpis:
    profits = [r for r in filtered if r.worst_case_profit is not None]
    best_profit_row = max(profits, key=lambda r: r.worst_case_profit, default=None)
    return Kpis(
        match_count=sum(match_in_scope(m, f, now) for m in report.matches),
        arbitrage_count=len(filtered),
        high_margin_count=sum(r.margin_pct >= HIGH_MARGIN_PCT for r in filtered),
        best_margin_pct=max((r.margin_pct for r in filtered), default=None),
        best_profit=best_profit_row.worst_case_profit if best_profit_row else None,
        best_profit_row=best_profit_row,
        last_update=last_update,
    )
