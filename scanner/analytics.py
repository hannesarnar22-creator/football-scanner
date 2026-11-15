"""Greining á sögulegum arbitrage-tækifærum.

Allar fyrirspurnir krefjast `mode` (DEMO eða LIVE) – þeim er aldrei blandað saman.
Mælingar byggja á nýjustu skráðu gildum hvers einstaks tækifæris.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from scanner.db import Database, DataMode, MatchRow, OpportunityRow
from scanner.feasibility import FeasibilityStatus

PERIODS: dict[str, timedelta] = {
    "Síðustu 24 klst.": timedelta(hours=24),
    "Síðustu 7 dagar": timedelta(days=7),
    "Síðustu 30 dagar": timedelta(days=30),
}
THRESHOLDS = (Decimal(2), Decimal(3), Decimal(5))
REFERENCE_TOTAL = Decimal(100_000)


@dataclass(frozen=True)
class HistoricalOpportunity:
    key: str
    mode: str
    match: str
    competition: str
    kickoff_utc: datetime
    market: str
    outcome_a: str
    outcome_b: str
    bookmaker_a: str
    bookmaker_b: str
    odds_a: Decimal
    odds_b: Decimal
    margin_pct: Decimal  # nýjasta fræðilega framlegð
    peak_margin_pct: Decimal
    real_margin_pct: Decimal | None
    worst_case_profit: Decimal | None
    total_stake: Decimal
    max_feasible_total: Decimal | None
    status: str
    data_basis: str
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool
    observations: int
    # (skráningartími, framlegð) fyrir hverja breytingu, elst fyrst
    margin_history: tuple[tuple[datetime, Decimal], ...] = ()


@dataclass(frozen=True)
class HistorySummary:
    unique_count: int
    over_2: int
    over_3: int
    over_5: int
    avg_margin_pct: Decimal | None
    max_margin_pct: Decimal | None
    meets_100k_count: int
    unconfirmed_count: int


def _since(now: datetime, period: timedelta | None) -> datetime | None:
    return None if period is None else now - period


def load_history(db: Database, mode: DataMode, now: datetime, period: timedelta | None) -> list[HistoricalOpportunity]:
    """Einstök tækifæri sem sáust innan tímabilsins (last_seen_at ≥ now − period)."""
    since = _since(now, period)
    q = (
        select(OpportunityRow)
        .where(OpportunityRow.mode == mode)
        .options(
            joinedload(OpportunityRow.match).joinedload(MatchRow.competition),
            joinedload(OpportunityRow.market),
            joinedload(OpportunityRow.bookmaker_a),
            joinedload(OpportunityRow.bookmaker_b),
            joinedload(OpportunityRow.observations),
        )
        .order_by(OpportunityRow.last_seen_at.desc())
    )
    if since is not None:
        q = q.where(OpportunityRow.last_seen_at >= since)
    with db.session() as s:
        rows = s.scalars(q).unique().all()
        return [
            HistoricalOpportunity(
                key=r.opp_key, mode=r.mode, match=r.match.label, competition=r.match.competition.name,
                kickoff_utc=r.match.kickoff_utc, market=r.market.label,
                outcome_a=r.outcome_a, outcome_b=r.outcome_b,
                bookmaker_a=r.bookmaker_a.name, bookmaker_b=r.bookmaker_b.name,
                odds_a=r.odds_a, odds_b=r.odds_b, margin_pct=r.theoretical_margin_pct,
                peak_margin_pct=r.peak_margin_pct, real_margin_pct=r.real_margin_pct,
                worst_case_profit=r.worst_case_profit, total_stake=r.total_stake,
                max_feasible_total=r.max_feasible_total, status=r.status, data_basis=r.data_basis,
                first_seen_at=r.first_seen_at, last_seen_at=r.last_seen_at, is_active=r.is_active,
                observations=len(r.observations),
                margin_history=tuple((o.observed_at, o.theoretical_margin_pct) for o in r.observations),
            )
            for r in rows
        ]


def summarize_history(items: list[HistoricalOpportunity]) -> HistorySummary:
    margins = [i.margin_pct for i in items]
    over = {t: sum(m > t for m in margins) for t in THRESHOLDS}
    return HistorySummary(
        unique_count=len(items),
        over_2=over[Decimal(2)],
        over_3=over[Decimal(3)],
        over_5=over[Decimal(5)],
        avg_margin_pct=sum(margins) / len(margins) if margins else None,
        max_margin_pct=max(margins, default=None),
        meets_100k_count=sum(
            i.status == FeasibilityStatus.MEETS.value and i.total_stake == REFERENCE_TOTAL for i in items
        ),
        unconfirmed_count=sum(i.status == FeasibilityStatus.UNCONFIRMED.value for i in items),
    )


# ---------------------------------------------------------------------------
# Tímaraðir
# ---------------------------------------------------------------------------

HOURLY_UP_TO = timedelta(hours=48)  # styttri tímabil eru sýnd eftir klukkustundum


@dataclass(frozen=True)
class Bucket:
    """Eitt tímabil á tímaás: [start, end)."""

    start: datetime
    end: datetime
    label: str


def _window_start(items: list[HistoricalOpportunity], now: datetime, period: timedelta | None) -> datetime:
    if period is not None:
        return now - period
    return min((i.first_seen_at for i in items), default=now)


def time_buckets(now: datetime, since: datetime, hourly: bool) -> list[Bucket]:
    """Klukkustundir eða dagar (UTC) sem ná yfir [since, now]."""
    out = []
    if hourly:
        start = since.replace(minute=0, second=0, microsecond=0)
        step = timedelta(hours=1)
        fmt = "%d.%m. %H:00"
    else:
        start = since.replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(days=1)
        fmt = "%d.%m.%Y"
    while start <= now:
        out.append(Bucket(start, start + step, start.strftime(fmt)))
        start += step
    return out


def is_hourly(period: timedelta | None) -> bool:
    return period is not None and period <= HOURLY_UP_TO


def _margin_at(item: HistoricalOpportunity, moment: datetime) -> Decimal:
    """Framlegð tækifæris eins og hún var skráð fyrir `moment`."""
    value = None
    for observed_at, margin in item.margin_history:
        if observed_at < moment:
            value = margin
    return item.margin_pct if value is None else value


@dataclass(frozen=True)
class MarginPoint:
    bucket: Bucket
    avg_margin_pct: Decimal | None  # None = engin tækifæri á tímabilinu (rof í línu)
    max_margin_pct: Decimal | None
    count: int


def margin_series(items: list[HistoricalOpportunity], now: datetime, period: timedelta | None) -> list[MarginPoint]:
    """Meðal- og hæsta framlegð þeirra tækifæra sem voru til á hverju tímabili.

    Tækifæri telst til á tímabili ef það sást fyrst fyrir lok þess og síðast eftir
    upphaf þess. Framlegðin er sú sem var skráð fyrir lok tímabilsins.
    """
    since = _window_start(items, now, period)
    points = []
    for b in time_buckets(now, since, is_hourly(period)):
        lo = max(b.start, since)
        alive = [i for i in items if i.first_seen_at < b.end and i.last_seen_at >= lo]
        values = [_margin_at(i, b.end) for i in alive]
        points.append(MarginPoint(
            bucket=b,
            avg_margin_pct=sum(values) / len(values) if values else None,
            max_margin_pct=max(values, default=None),
            count=len(values),
        ))
    return points


def daily_new_counts(
    items: list[HistoricalOpportunity], now: datetime, period: timedelta | None
) -> list[tuple[str, int]]:
    """(dagsetning 'dd.mm.yyyy', fjöldi) – ný einstök tækifæri eftir dögum innan tímabilsins.

    Allir dagar tímabilsins eru með (0 ef ekkert nýtt). Tækifæri sem sáust fyrst
    áður en tímabilið hófst teljast ekki ný innan þess.
    """
    since = _window_start(items, now, period)
    counts: dict[date, int] = defaultdict(int)
    for i in items:
        if since <= i.first_seen_at <= now:
            counts[i.first_seen_at.date()] += 1
    return [(b.label, counts.get(b.start.date(), 0)) for b in time_buckets(now, since, hourly=False)]


CSV_COLUMNS = [
    ("Auðkenni", "key"), ("Hamur", "mode"), ("Leikur", "match"), ("Keppni", "competition"),
    ("Upphaf (UTC)", "kickoff_utc"), ("Markaður", "market"),
    ("Valkostur A", "outcome_a"), ("Síða A", "bookmaker_a"), ("Stuðull A", "odds_a"),
    ("Valkostur B", "outcome_b"), ("Síða B", "bookmaker_b"), ("Stuðull B", "odds_b"),
    ("Framlegð %", "margin_pct"), ("Hæsta framlegð %", "peak_margin_pct"), ("Raunframlegð %", "real_margin_pct"),
    ("Lægsti hagnaður ISK", "worst_case_profit"), ("Heildarfjárhæð ISK", "total_stake"),
    ("Hæsta mögulega fjárhæð ISK", "max_feasible_total"), ("Staða", "status"), ("Grunnur", "data_basis"),
    ("Fyrst séð (UTC)", "first_seen_at"), ("Síðast séð (UTC)", "last_seen_at"), ("Virkt", "is_active"),
    ("Fjöldi athugana", "observations"),
]


def _csv_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Já" if v else "Nei"
    if isinstance(v, Decimal):
        # Í mesta lagi 4 aukastafir, engin vísindaleg framsetning (100000, ekki 1E+5)
        return f"{v.quantize(Decimal('0.0001')).normalize():f}".replace(".", ",")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def to_csv(items: list[HistoricalOpportunity]) -> bytes:
    """CSV fyrir íslenskt Excel: semíkomma sem skil, komma sem tugabrotskil, UTF-8 með BOM."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow([h for h, _ in CSV_COLUMNS])
    for i in items:
        w.writerow([_csv_value(getattr(i, attr)) for _, attr in CSV_COLUMNS])
    return buf.getvalue().encode("utf-8-sig")
