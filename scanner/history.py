"""Vistun leitarniðurstaðna í gagnagrunn (saga stuðla og tækifæra).

Idempotent: sami innlestur tvisvar breytir engu nema `last_seen_at`.
  - Stuðull: ný lína (snapshot) aðeins ef verð breytist frá síðasta snapshot.
  - Tækifæri: stöðugt auðkenni (leikur, markaður, lína, valkostir, síður, reglur).
    Sama tækifæri með óbreytta stuðla telst EKKI nýtt.
  - Athugun (observation): ný lína aðeins ef stuðlar eða mat breytist.
  - DEMO og LIVE: innlestur hafnar gögnum úr röngum ham.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scanner.db import (
    BookmakerRow,
    CompetitionRow,
    Database,
    DataMode,
    MarketRow,
    MatchRow,
    OddsSnapshotRow,
    OpportunityObservationRow,
    OpportunityRow,
)
from scanner.models import Bookmaker, Market, Match, OddsQuote, Outcome
from scanner.scan import Opportunity, ScanReport


def mode_of_quote(q: OddsQuote) -> DataMode:
    return DataMode.DEMO if q.source.is_demo else DataMode.LIVE


def make_opportunity_key(
    match_id: str, market: Market, outcome_a: Outcome, outcome_b: Outcome, bookmaker_a: str, bookmaker_b: str
) -> str:
    """Stöðugt auðkenni tækifæris. Stuðlar eru EKKI hluti af því – breyttir
    stuðlar á sama pari eru sama tækifærið."""
    line = "" if market.line is None else f"{market.line:f}"
    parts = (
        match_id, market.type.name, line, market.rules.period.name, market.rules.total_type.name,
        outcome_a.name, outcome_b.name, bookmaker_a, bookmaker_b,
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def opportunity_key(o: Opportunity) -> str:
    return make_opportunity_key(
        o.match.match_id, o.market, o.quote_a.outcome, o.quote_b.outcome, o.bookmaker_a.id, o.bookmaker_b.id
    )


@dataclass(frozen=True)
class IngestResult:
    new_snapshots: int
    unchanged_quotes: int
    new_opportunities: int
    updated_opportunities: int
    new_observations: int
    deactivated: int


class _Rows:
    """Get-or-create fyrir víddartöflur innan einnar færslu."""

    def __init__(self, s: Session, mode: DataMode, bookmakers: dict[str, Bookmaker]) -> None:
        self.s, self.mode, self.bookmakers = s, mode, bookmakers
        self._comp: dict[str, CompetitionRow] = {}
        self._bm: dict[str, BookmakerRow] = {}
        self._market: dict[Market, MarketRow] = {}
        self._match: dict[str, MatchRow] = {}

    def competition(self, m: Match) -> CompetitionRow:
        row = self._comp.get(m.competition_id) or self.s.get(CompetitionRow, m.competition_id)
        if row is None:
            row = CompetitionRow(id=m.competition_id, name=m.competition_name)
            self.s.add(row)
        self._comp[m.competition_id] = row
        return row

    def bookmaker(self, bm_id: str) -> BookmakerRow:
        row = self._bm.get(bm_id) or self.s.get(BookmakerRow, bm_id)
        if row is None:
            bm = self.bookmakers.get(bm_id)
            row = BookmakerRow(id=bm_id, name=bm.name if bm else bm_id)
            self.s.add(row)
        self._bm[bm_id] = row
        return row

    def market(self, market: Market) -> MarketRow:
        if market in self._market:
            return self._market[market]
        line = "" if market.line is None else f"{market.line:f}"
        row = self.s.scalar(select(MarketRow).where(
            MarketRow.type == market.type.name, MarketRow.line == line,
            MarketRow.period == market.rules.period.name, MarketRow.total_type == market.rules.total_type.name,
        ))
        if row is None:
            row = MarketRow(type=market.type.name, line=line, period=market.rules.period.name,
                            total_type=market.rules.total_type.name, label=market.label)
            self.s.add(row)
            self.s.flush()
        self._market[market] = row
        return row

    def match(self, m: Match) -> MatchRow:
        if m.match_id in self._match:
            return self._match[m.match_id]
        row = self.s.scalar(select(MatchRow).where(MatchRow.mode == self.mode, MatchRow.match_key == m.match_id))
        if row is None:
            self.competition(m)
            row = MatchRow(
                mode=self.mode, match_key=m.match_id, competition_id=m.competition_id,
                home_team_id=m.home_team_id, home_team_name=m.home_team_name,
                away_team_id=m.away_team_id, away_team_name=m.away_team_name, kickoff_utc=m.kickoff_utc,
            )
            self.s.add(row)
            self.s.flush()
        self._match[m.match_id] = row
        return row


def _check_mode(report: ScanReport, mode: DataMode) -> None:
    wrong = [q for _, q in report.valid_quotes if mode_of_quote(q) is not mode]
    wrong += [q for o in report.arbitrage for q in (o.quote_a, o.quote_b) if mode_of_quote(q) is not mode]
    if wrong:
        raise ValueError(
            f"{len(wrong)} stuðlar passa ekki við ham {mode} (t.d. {mode_of_quote(wrong[0])}) – "
            "DEMO og LIVE má ekki blanda saman"
        )


def _latest_snapshots(s: Session, mode: DataMode) -> dict[tuple, OddsSnapshotRow]:
    newest = (
        select(func.max(OddsSnapshotRow.id))
        .where(OddsSnapshotRow.mode == mode)
        .group_by(OddsSnapshotRow.match_id, OddsSnapshotRow.market_id,
                  OddsSnapshotRow.outcome, OddsSnapshotRow.bookmaker_id)
    )
    rows = s.scalars(select(OddsSnapshotRow).where(OddsSnapshotRow.id.in_(newest)))
    return {(r.match_id, r.market_id, r.outcome, r.bookmaker_id): r for r in rows}


def _observation_values(o: Opportunity) -> tuple:
    f = o.feasibility
    return (o.quote_a.price, o.quote_b.price, f.status.value, f.worst_case_profit, f.requested_total, f.real_margin_pct)


def _apply_latest(row: OpportunityRow, o: Opportunity) -> None:
    f = o.feasibility
    la, lb = o.bookmaker_a.limits, o.bookmaker_b.limits
    row.odds_a, row.odds_b = o.quote_a.price, o.quote_b.price
    row.theoretical_margin_pct = f.theoretical_margin_pct
    row.real_margin_pct = f.real_margin_pct
    row.worst_case_profit = f.worst_case_profit
    row.total_stake = f.requested_total
    row.max_feasible_total = f.max_feasible_total
    row.status = f.status.value
    row.data_basis = f.data_basis.value
    row.max_stake_a, row.max_stake_b = la.max_stake, lb.max_stake
    row.balance_a, row.balance_b = la.available_balance, lb.available_balance
    row.currency_a, row.currency_b = la.currency, lb.currency


def ingest_scan(
    db: Database,
    report: ScanReport,
    bookmakers: dict[str, Bookmaker],
    mode: DataMode,
    now: datetime,
) -> IngestResult:
    """Vistar niðurstöður einnar leitar. Má kalla oft með sömu gögnum."""
    _check_mode(report, mode)
    counts = dict(new_snapshots=0, unchanged_quotes=0, new_opportunities=0,
                  updated_opportunities=0, new_observations=0, deactivated=0)

    with db.session() as s, s.begin():
        rows = _Rows(s, mode, bookmakers)

        # --- Saga stuðla ---
        latest = _latest_snapshots(s, mode)
        for match, q in report.valid_quotes:
            m_row, mk_row = rows.match(match), rows.market(q.market)
            rows.bookmaker(q.bookmaker_id)
            key = (m_row.id, mk_row.id, q.outcome.name, q.bookmaker_id)
            prev = latest.get(key)
            if prev is not None and prev.price == q.price:
                prev.last_seen_at = max(prev.last_seen_at, now)
                counts["unchanged_quotes"] += 1
                continue
            snap = OddsSnapshotRow(
                mode=mode, match_id=m_row.id, market_id=mk_row.id, outcome=q.outcome.name,
                bookmaker_id=q.bookmaker_id, price=q.price, source_time=q.timestamp.effective,
                recorded_at=now, last_seen_at=now,
            )
            s.add(snap)
            latest[key] = snap
            counts["new_snapshots"] += 1

        # --- Tækifæri ---
        existing = {r.opp_key: r for r in s.scalars(select(OpportunityRow).where(OpportunityRow.mode == mode))}
        seen: set[str] = set()
        for o in report.arbitrage:
            key = opportunity_key(o)
            if key in seen:
                continue
            seen.add(key)
            row = existing.get(key)
            if row is None:
                row = OpportunityRow(
                    mode=mode, opp_key=key,
                    match_id=rows.match(o.match).id, market_id=rows.market(o.market).id,
                    outcome_a=o.quote_a.outcome.name, outcome_b=o.quote_b.outcome.name,
                    bookmaker_a_id=rows.bookmaker(o.bookmaker_a.id).id,
                    bookmaker_b_id=rows.bookmaker(o.bookmaker_b.id).id,
                    first_seen_at=now, last_seen_at=now, is_active=True,
                    peak_margin_pct=o.margin_pct,
                )
                _apply_latest(row, o)  # áður en línan fer í lotuna (öll gildi skilyrt)
                s.add(row)
                existing[key] = row
                counts["new_opportunities"] += 1
            else:
                row.first_seen_at = min(row.first_seen_at, now)
                row.last_seen_at = max(row.last_seen_at, now)
                row.is_active = True
                row.peak_margin_pct = max(row.peak_margin_pct, o.margin_pct)
                _apply_latest(row, o)
                counts["updated_opportunities"] += 1

            last = row.observations[-1] if row.observations else None
            values = _observation_values(o)
            if last is None or (last.odds_a, last.odds_b, last.status, last.worst_case_profit,
                                last.total_stake, last.real_margin_pct) != values:
                s.add(OpportunityObservationRow(
                    opportunity=row, observed_at=now,
                    odds_a=o.quote_a.price, odds_b=o.quote_b.price,
                    source_time_a=o.quote_a.timestamp.effective, source_time_b=o.quote_b.timestamp.effective,
                    theoretical_margin_pct=o.margin_pct, real_margin_pct=o.feasibility.real_margin_pct,
                    worst_case_profit=o.feasibility.worst_case_profit,
                    total_stake=o.feasibility.requested_total, status=o.feasibility.status.value,
                ))
                counts["new_observations"] += 1

        # --- Tækifæri sem sjást ekki lengur ---
        for key, row in existing.items():
            if key not in seen and row.is_active:
                row.is_active = False
                counts["deactivated"] += 1

    return IngestResult(**counts)
