"""Samræming liða- og keppnisheita milli veðmálasíðna.

Röð aðferða (sú fyrsta sem tekst gildir):
  1. Staðlaður liðakóði (t.d. MUN) – aðeins ef hann stangast ekki á við heitið
  2. Nákvæm samsvörun við alias-skrá (eftir stöðlun) innan keppninnar
  3. Varfærið fuzzy matching innan keppninnar:
       - samþykkt aðeins ef líkindi ≥ FUZZY_ACCEPT og skýr munur á næsta kosti
       - annars merkt ÓVÍST og EKKI samþykkt sjálfkrafa
Keppnir eru aðeins samræmdar með nákvæmri alias-samsvörun.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

DEFAULT_ALIAS_FILE = Path(__file__).resolve().parent.parent / "data" / "teams.json"

FUZZY_ACCEPT = 0.88  # lágmarkslíkindi til að samþykkja fuzzy
FUZZY_MARGIN = 0.10  # lágmarksmunur á besta og næstbesta kosti
FUZZY_CANDIDATE = 0.50  # undir þessu er talið að ekkert lið finnist

_TRANSLIT = str.maketrans({"ð": "d", "þ": "th", "æ": "ae", "ø": "o", "œ": "oe", "ł": "l", "đ": "d"})
_NOISE_TOKENS = frozenset({"fc", "cf", "afc", "ssc", "sc", "acf", "bc", "ss", "as", "ac", "club", "de", "the"})


def normalize_name(name: str) -> str:
    """'Manchester United FC' -> 'manchester united', 'Breiðablik' -> 'breidablik'."""
    s = name.casefold().translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(t for t in s.split() if t not in _NOISE_TOKENS)


def similarity(a: str, b: str) -> float:
    """Líkindi tveggja staðlaðra heita (0–1). Tekur hæsta gildi af beinum og
    orðaröðuðum samanburði svo 'united manchester' = 'manchester united'."""
    direct = SequenceMatcher(None, a, b).ratio()
    sorted_a, sorted_b = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    return max(direct, SequenceMatcher(None, sorted_a, sorted_b).ratio())


@dataclass(frozen=True)
class Competition:
    id: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Team:
    id: str
    code: str | None
    competition_id: str
    name: str
    aliases: tuple[str, ...]

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class MatchMethod(StrEnum):
    CODE = "Liðakóði"
    EXACT = "Alias-skrá"
    FUZZY = "Fuzzy (öruggt)"
    UNCERTAIN = "Óvíst – þarf yfirferð"
    NOT_FOUND = "Fannst ekki"


@dataclass(frozen=True)
class TeamResolution:
    query: str
    team: Team | None
    method: MatchMethod
    score: float
    candidates: tuple[tuple[str, float], ...] = ()

    @property
    def accepted(self) -> bool:
        return self.team is not None

    @property
    def is_fuzzy(self) -> bool:
        return self.method is MatchMethod.FUZZY


class TeamRegistry:
    def __init__(self, competitions: list[Competition], teams: list[Team]) -> None:
        self.competitions = {c.id: c for c in competitions}
        self.teams = {t.id: t for t in teams}
        self._competition_by_name: dict[str, Competition] = {}
        for c in competitions:
            for n in (c.name, *c.aliases, c.id):
                self._add_unique(self._competition_by_name, normalize_name(n), c, f"keppni '{n}'")
        self._team_by_code = {t.code.upper(): t for t in teams if t.code}
        # (keppni, staðlað heiti) -> lið
        self._team_by_name: dict[tuple[str, str], Team] = {}
        for t in teams:
            if t.competition_id not in self.competitions:
                raise ValueError(f"Lið {t.id}: óþekkt keppni {t.competition_id}")
            for n in t.all_names:
                self._add_unique(self._team_by_name, (t.competition_id, normalize_name(n)), t, f"lið '{n}'")

    @staticmethod
    def _add_unique(index: dict, key, value, what: str) -> None:
        existing = index.get(key)
        if existing is not None and existing != value:
            raise ValueError(f"Tvírætt alias í skrá: {what}")
        index[key] = value

    @classmethod
    def from_file(cls, path: Path = DEFAULT_ALIAS_FILE) -> TeamRegistry:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        competitions = [
            Competition(id=c["id"], name=c["name"], aliases=tuple(c.get("aliases", ())))
            for c in data["competitions"]
        ]
        teams = [
            Team(
                id=t["id"],
                code=t.get("code"),
                competition_id=t["competition"],
                name=t["name"],
                aliases=tuple(t.get("aliases", ())),
            )
            for t in data["teams"]
        ]
        return cls(competitions, teams)

    # ------------------------------------------------------------------

    def resolve_competition(self, name: str) -> Competition | None:
        """Aðeins nákvæm samsvörun – engin fuzzy fyrir keppnir."""
        return self._competition_by_name.get(normalize_name(name))

    def resolve_team(self, name: str, competition_id: str, code: str | None = None) -> TeamResolution:
        norm = normalize_name(name)
        exact = self._team_by_name.get((competition_id, norm))

        if code:
            by_code = self._team_by_code.get(code.upper())
            if by_code is not None and by_code.competition_id == competition_id:
                if exact is not None and exact != by_code:
                    # Kóði og heiti benda á ólík lið -> ekki treysta hvoru
                    return TeamResolution(
                        name, None, MatchMethod.UNCERTAIN, 0.0,
                        ((by_code.name, 1.0), (exact.name, 1.0)),
                    )
                return TeamResolution(name, by_code, MatchMethod.CODE, 1.0)

        if exact is not None:
            return TeamResolution(name, exact, MatchMethod.EXACT, 1.0)

        scored: list[tuple[float, Team]] = []
        for team in self.teams.values():
            if team.competition_id != competition_id:
                continue
            best = max(similarity(norm, normalize_name(n)) for n in team.all_names)
            scored.append((best, team))
        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            return TeamResolution(name, None, MatchMethod.NOT_FOUND, 0.0)

        top_score, top_team = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        candidates = tuple((t.name, round(s, 3)) for s, t in scored[:3])

        if top_score >= FUZZY_ACCEPT and top_score - second >= FUZZY_MARGIN:
            return TeamResolution(name, top_team, MatchMethod.FUZZY, top_score, candidates)
        if top_score >= FUZZY_CANDIDATE:
            return TeamResolution(name, None, MatchMethod.UNCERTAIN, top_score, candidates)
        return TeamResolution(name, None, MatchMethod.NOT_FOUND, top_score, candidates)


@lru_cache(maxsize=1)
def default_registry() -> TeamRegistry:
    return TeamRegistry.from_file()
