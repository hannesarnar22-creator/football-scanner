"""Arbitrage-leit yfir samræmda leiki.

Ferlið fyrir hvern leik:
  1. Útiloka leiki sem eru hafnir (eingöngu pre-match)
  2. Útiloka óstudda markaði, úrelta stuðla og stuðla frá óþekktum síðum
  3. Fjarlægja tvítekna stuðla (sama síða, markaður og valkostur): nýjasti gildir
  4. Fyrir hvern markað: velja hæsta stuðul á hvorn valkost frá TVEIMUR ólíkum síðum
  5. Meta framkvæmdarhæfni (áfangi 4) og merkja óstaðfestar forsendur
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from scanner.feasibility import (
    DEFAULT_MAX_LIMITS_AGE,
    FeasibilityResult,
    FeasibilityStatus,
    assess_feasibility,
)
from scanner.matching import MatchedEvent, ReviewItem, match_events
from scanner.models import (
    OUTCOME_PAIRS,
    Bookmaker,
    BookmakerEvent,
    DataOrigin,
    Market,
    Match,
    OddsQuote,
    Outcome,
)
from scanner.teams import TeamRegistry

DEFAULT_MAX_QUOTE_AGE = timedelta(minutes=5)
# Stuðlar teljast samtímis ef tímastimplar eru innan þessa glugga
DEFAULT_SYNC_WINDOW = timedelta(seconds=120)
# Leyfð klukkuskekkja fyrir tímastimpla "í framtíðinni"
CLOCK_SKEW = timedelta(seconds=30)

ONE = Decimal(1)


@dataclass(frozen=True)
class ExcludedQuote:
    match_label: str
    bookmaker_id: str
    market_label: str
    outcome: str
    reason: str


@dataclass(frozen=True)
class Opportunity:
    match: Match
    market: Market
    quote_a: OddsQuote  # Over / Yes
    quote_b: OddsQuote  # Under / No
    bookmaker_a: Bookmaker
    bookmaker_b: Bookmaker
    feasibility: FeasibilityResult
    assumptions: tuple[str, ...]

    @property
    def key(self) -> str:
        """Fast auðkenni: einn leikur + einn markaður = eitt tækifæri."""
        return f"{self.match.match_id}|{self.market.label}"

    @property
    def status(self) -> FeasibilityStatus:
        return self.feasibility.status

    @property
    def margin_pct(self) -> Decimal:
        return self.feasibility.theoretical_margin_pct

    @property
    def data_basis(self) -> DataOrigin:
        return self.feasibility.data_basis

    @property
    def is_demo(self) -> bool:
        return self.data_basis is DataOrigin.DEMO


@dataclass
class ScanReport:
    opportunities: list[Opportunity] = field(default_factory=list)
    excluded: list[ExcludedQuote] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    # Gildir stuðlar (studdir, nýir, pre-match, án tvítekninga) – vistaðir í sögu
    valid_quotes: list[tuple[Match, OddsQuote]] = field(default_factory=list)
    # Pre-match leikir sem voru skannaðir (einn á hvern leik, óháð ólíkum upphafstíma)
    matches: list[Match] = field(default_factory=list)
    match_count: int = 0
    last_updated: datetime | None = None

    @property
    def arbitrage(self) -> list[Opportunity]:
        return [o for o in self.opportunities if o.feasibility.is_arbitrage]


@dataclass(frozen=True)
class _Candidate:
    quote: OddsQuote
    fuzzy: bool


def _pick_newest(existing: _Candidate, new: _Candidate) -> _Candidate:
    """Nýjasti stuðull gildir. Ef jafngamlir: lægri stuðull (varfærið)."""
    a, b = existing.quote, new.quote
    if b.timestamp.effective != a.timestamp.effective:
        return new if b.timestamp.effective > a.timestamp.effective else existing
    return new if b.price < a.price else existing


def _valid_candidates(
    group: MatchedEvent,
    bookmakers: dict[str, Bookmaker],
    now: datetime,
    max_age: timedelta,
    excluded: list[ExcludedQuote],
) -> dict[tuple[Market, Outcome, str], _Candidate]:
    label = group.match.label
    best: dict[tuple[Market, Outcome, str], _Candidate] = {}

    def exclude(q: OddsQuote, reason: str) -> None:
        excluded.append(ExcludedQuote(label, q.bookmaker_id, q.market.label, q.outcome.value, reason))

    for event in group.events:
        fuzzy = (event.bookmaker_id, event.event_id) in group.fuzzy_events
        for q in event.quotes:
            if q.bookmaker_id != event.bookmaker_id:
                exclude(q, "Stuðull tilheyrir annarri veðmálasíðu en leikurinn")
                continue
            if q.bookmaker_id not in bookmakers:
                exclude(q, "Óþekkt veðmálasíða")
                continue
            reason = q.market.unsupported_reason()
            if reason:
                exclude(q, reason)
                continue
            age = q.timestamp.age(now)
            if age > max_age:
                exclude(q, f"Úreltur stuðull ({int(age.total_seconds() // 60)} mín. gamall)")
                continue
            if age < -CLOCK_SKEW:
                exclude(q, "Tímastimpill er í framtíðinni")
                continue

            key = (q.market, q.outcome, q.bookmaker_id)
            cand = _Candidate(q, fuzzy)
            if key in best:
                kept = _pick_newest(best[key], cand)
                dropped = cand if kept is best[key] else best[key]
                exclude(dropped.quote, "Tvítekinn stuðull – nýjasti notaður")
                best[key] = kept
            else:
                best[key] = cand
    return best


def _best_pair(
    side_a: list[_Candidate], side_b: list[_Candidate]
) -> tuple[_Candidate, _Candidate] | None:
    """Hæstu stuðlar á hvorn valkost, krafa um tvær ólíkar veðmálasíður.

    Lágmarkar S = 1/A + 1/B. Ef jafnt: minnstur tímamunur á stuðlum.
    """
    best = None
    best_key = None
    for a in side_a:
        for b in side_b:
            if a.quote.bookmaker_id == b.quote.bookmaker_id:
                continue
            s = ONE / a.quote.price + ONE / b.quote.price
            gap = abs(a.quote.timestamp.effective - b.quote.timestamp.effective)
            key = (s, gap, a.quote.bookmaker_id, b.quote.bookmaker_id)
            if best_key is None or key < best_key:
                best, best_key = (a, b), key
    return best


def scan_events(
    events: list[BookmakerEvent],
    bookmakers: dict[str, Bookmaker],
    now: datetime,
    total_stake: int | Decimal = 100_000,
    registry: TeamRegistry | None = None,
    max_age: timedelta = DEFAULT_MAX_QUOTE_AGE,
    sync_window: timedelta = DEFAULT_SYNC_WINDOW,
    fx_rates: Mapping[str, Decimal] | None = None,
    max_limits_age: timedelta = DEFAULT_MAX_LIMITS_AGE,
) -> ScanReport:
    ctx = _Context(bookmakers, now, total_stake, sync_window, fx_rates or {}, max_limits_age)
    matching = match_events(events, registry)
    report = ScanReport(review=list(matching.review))
    counted: set[tuple[str, str, str]] = set()

    for group in matching.matched:
        match = group.match
        if match.kickoff_utc <= now:
            for e in group.events:
                for q in e.quotes:
                    report.excluded.append(
                        ExcludedQuote(match.label, q.bookmaker_id, q.market.label, q.outcome.value,
                                      "Leikur hafinn – eingöngu pre-match")
                    )
            continue
        # Leikur með ólíkan upphafstíma hjá síðum er samt einn leikur í talningu
        teams_key = (match.competition_id, match.home_team_id, match.away_team_id)
        if teams_key not in counted:
            counted.add(teams_key)
            report.matches.append(match)
        report.match_count = len(counted)

        candidates = _valid_candidates(group, bookmakers, now, max_age, report.excluded)

        by_market: dict[Market, dict[Outcome, list[_Candidate]]] = defaultdict(lambda: defaultdict(list))
        for (market, outcome, _), cand in candidates.items():
            by_market[market][outcome].append(cand)
            report.valid_quotes.append((match, cand.quote))
            ts = cand.quote.timestamp.effective
            if report.last_updated is None or ts > report.last_updated:
                report.last_updated = ts

        for market, outcomes in by_market.items():
            out_a, out_b = OUTCOME_PAIRS[market.type]
            pair = _best_pair(outcomes.get(out_a, []), outcomes.get(out_b, []))
            if pair is None:
                continue
            report.opportunities.append(_build_opportunity(group, market, pair, ctx))

    report.opportunities.sort(key=lambda o: o.margin_pct, reverse=True)
    return report


@dataclass(frozen=True)
class _Context:
    bookmakers: dict[str, Bookmaker]
    now: datetime
    total_stake: int | Decimal
    sync_window: timedelta
    fx_rates: Mapping[str, Decimal]
    max_limits_age: timedelta


def _build_opportunity(
    group: MatchedEvent,
    market: Market,
    pair: tuple[_Candidate, _Candidate],
    ctx: _Context,
) -> Opportunity:
    a, b = pair
    qa, qb = a.quote, b.quote
    bm_a, bm_b = ctx.bookmakers[qa.bookmaker_id], ctx.bookmakers[qb.bookmaker_id]

    # Óvissa um samsvörun -> Óstaðfest
    unconfirmed: list[str] = []
    for cand, bm in ((a, bm_a), (b, bm_b)):
        if cand.fuzzy:
            unconfirmed.append(f"Liðsheiti hjá {bm.name} samræmt með fuzzy matching")
    unconfirmed.extend(group.notes)

    # Stuðlar ekki sóttir á sama tíma -> ósambærilegir
    incomparable: list[str] = []
    gap = abs(qa.timestamp.effective - qb.timestamp.effective)
    if gap > ctx.sync_window:
        incomparable.append(f"Stuðlar ekki samtímis: {int(gap.total_seconds())} sek. á milli tímastimpla")

    feasibility = assess_feasibility(
        qa.price, qb.price, bm_a.limits, bm_b.limits, ctx.total_stake,
        now=ctx.now,
        fx_rates=ctx.fx_rates,
        max_limits_age=ctx.max_limits_age,
        odds_origin=DataOrigin.weakest(qa.source.origin, qb.source.origin),
        extra_unconfirmed=tuple(unconfirmed),
        extra_incomparable=tuple(incomparable),
        label_a=f"{bm_a.name} ({qa.outcome})",
        label_b=f"{bm_b.name} ({qb.outcome})",
    )

    return Opportunity(
        match=group.match,
        market=market,
        quote_a=qa,
        quote_b=qb,
        bookmaker_a=bm_a,
        bookmaker_b=bm_b,
        feasibility=feasibility,
        assumptions=tuple(unconfirmed + incomparable),
    )
