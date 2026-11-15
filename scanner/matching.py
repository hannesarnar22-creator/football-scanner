"""Samsvörun leikja milli veðmálasíðna.

Leikir eru aðeins sameinaðir ef keppni, heimalið, útilið OG upphafstími (UTC)
eru nákvæmlega eins eftir samræmingu. Leikir sem passa ekki örugglega fara á
yfirferðarlista og eru ekki notaðir í arbitrage-leit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from scanner.models import BookmakerEvent, Match
from scanner.teams import TeamRegistry, TeamResolution, default_registry

# Sömu lið með ólíkan upphafstíma innan þessa glugga teljast hugsanlega sami leikur
KICKOFF_CONFLICT_WINDOW = timedelta(hours=72)


@dataclass(frozen=True)
class ReviewItem:
    """Gögn sem þarf að fara yfir handvirkt áður en þau eru notuð."""

    bookmaker_id: str
    event_id: str
    description: str
    reason: str


@dataclass
class MatchedEvent:
    match: Match
    events: list[BookmakerEvent] = field(default_factory=list)
    # (bookmaker_id, event_id) fyrir viðburði þar sem fuzzy matching var notað
    fuzzy_events: set[tuple[str, str]] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def bookmaker_ids(self) -> set[str]:
        return {e.bookmaker_id for e in self.events}


@dataclass
class MatchingResult:
    matched: list[MatchedEvent]
    review: list[ReviewItem]


def _describe(e: BookmakerEvent) -> str:
    return f"{e.home_team} – {e.away_team} ({e.competition}, {e.kickoff_utc:%d.%m. %H:%M} UTC)"


def _resolution_problem(side: str, r: TeamResolution) -> str:
    if r.candidates:
        cands = ", ".join(f"{n} ({s:.0%})" for n, s in r.candidates[:2])
        return f"{side} '{r.query}': {r.method} – mögulegt: {cands}"
    return f"{side} '{r.query}': {r.method}"


def match_events(events: list[BookmakerEvent], registry: TeamRegistry | None = None) -> MatchingResult:
    registry = registry or default_registry()
    review: list[ReviewItem] = []
    groups: dict[tuple[str, str, str, datetime], MatchedEvent] = {}

    for e in events:
        comp = registry.resolve_competition(e.competition)
        if comp is None:
            review.append(ReviewItem(e.bookmaker_id, e.event_id, _describe(e), f"Óþekkt keppni '{e.competition}'"))
            continue

        home = registry.resolve_team(e.home_team, comp.id, e.home_code)
        away = registry.resolve_team(e.away_team, comp.id, e.away_code)
        problems = [_resolution_problem(s, r) for s, r in (("Heimalið", home), ("Útilið", away)) if not r.accepted]
        if problems:
            review.append(ReviewItem(e.bookmaker_id, e.event_id, _describe(e), "; ".join(problems)))
            continue
        assert home.team is not None and away.team is not None
        if home.team.id == away.team.id:
            review.append(ReviewItem(e.bookmaker_id, e.event_id, _describe(e), "Sama lið sem heima- og útilið"))
            continue

        key = (comp.id, home.team.id, away.team.id, e.kickoff_utc)
        group = groups.get(key)
        if group is None:
            match = Match(
                match_id=f"{comp.id}-{home.team.id}-{away.team.id}-{e.kickoff_utc:%Y%m%d%H%M}",
                competition_id=comp.id,
                competition_name=comp.name,
                home_team_id=home.team.id,
                home_team_name=home.team.name,
                away_team_id=away.team.id,
                away_team_name=away.team.name,
                kickoff_utc=e.kickoff_utc,
            )
            group = groups[key] = MatchedEvent(match=match)
        group.events.append(e)
        if home.is_fuzzy or away.is_fuzzy:
            group.fuzzy_events.add((e.bookmaker_id, e.event_id))

    _flag_kickoff_conflicts(list(groups.values()), review)
    return MatchingResult(matched=list(groups.values()), review=review)


def _flag_kickoff_conflicts(groups: list[MatchedEvent], review: list[ReviewItem]) -> None:
    """Sömu lið, ólíkur upphafstími: ekki sameinað, en merkt á báðum stöðum."""
    by_teams: dict[tuple[str, str, str], list[MatchedEvent]] = defaultdict(list)
    for g in groups:
        m = g.match
        by_teams[(m.competition_id, m.home_team_id, m.away_team_id)].append(g)

    for same_teams in by_teams.values():
        if len(same_teams) < 2:
            continue
        for g in same_teams:
            others = [
                o for o in same_teams
                if o is not g and abs(o.match.kickoff_utc - g.match.kickoff_utc) <= KICKOFF_CONFLICT_WINDOW
            ]
            if not others:
                continue
            other_desc = ", ".join(
                f"{', '.join(sorted(o.bookmaker_ids))} {o.match.kickoff_utc:%d.%m. %H:%M}" for o in others
            )
            g.notes.append(f"Ólíkur upphafstími hjá öðrum veðmálasíðum ({other_desc} UTC) – ekki sameinað")
            for e in g.events:
                review.append(
                    ReviewItem(
                        e.bookmaker_id, e.event_id, _describe(e),
                        f"Upphafstími stangast á við aðrar veðmálasíður ({other_desc} UTC)",
                    )
                )
