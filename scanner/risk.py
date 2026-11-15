"""Áhættumat tækifæra (áfangi 6).

Byggir á framkvæmdarhæfnimatinu (áfangi 4) en skiptir "Úrelt eða ósambærilegt"
í tvennt og bætir við aldri stuðla. Ekkert tækifæri er nokkurn tímann merkt
öruggt eða áhættulaust.

Forgangsröð (fyrsta sem á við gildir):
  1. Ósambærilegir markaðir – markaðir/reglur ekki eins, eða fjárhæðir ekki sambærilegar (vantar gengi)
  2. Úrelt – stuðlar of gamlir, ekki samtímis, eða veðmálamörk ekki staðfest nýlega
  3. Uppfyllir ekki skilyrði – brýtur hámark/lágmark/inneign eða hagnaður ≤ 0
  4. Óstaðfest – einhverjar upplýsingar UNKNOWN eða óviss samsvörun
  5. Uppfyllir skráð skilyrði
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from scanner.feasibility import FeasibilityStatus
from scanner.scan import DEFAULT_MAX_QUOTE_AGE, Opportunity

RISK_DISCLAIMER = (
    "Ekkert tækifæri er öruggt eða áhættulaust. Stuðlar geta breyst, veðmálum getur verið hafnað "
    "eða þau takmörkuð, og skráð mörk geta verið röng. Uppfyllt skilyrði þýða ekki að veðmálasíða "
    "samþykki tiltekna fjárhæð nema það hafi verið staðfest."
)


class RiskStatus(StrEnum):
    MEETS = "Uppfyllir skráð skilyrði"
    UNCONFIRMED = "Óstaðfest"
    STALE = "Úrelt"
    FAILS = "Uppfyllir ekki skilyrði"
    INCOMPARABLE = "Ósambærilegir markaðir"

    @property
    def icon(self) -> str:
        return {
            RiskStatus.MEETS: "🟢",
            RiskStatus.UNCONFIRMED: "🟡",
            RiskStatus.STALE: "🔴",
            RiskStatus.FAILS: "🔴",
            RiskStatus.INCOMPARABLE: "⛔",
        }[self]

    @property
    def color(self) -> str:
        """Grænt = staðfest jákvætt, gult = óstaðfest, rautt = úrelt/óuppfyllt."""
        return {
            RiskStatus.MEETS: "green",
            RiskStatus.UNCONFIRMED: "yellow",
        }.get(self, "red")

    @property
    def label(self) -> str:
        return f"{self.icon} {self.value}"


@dataclass(frozen=True)
class RiskAssessment:
    status: RiskStatus
    reasons: tuple[str, ...]
    odds_age: timedelta


def odds_age(o: Opportunity, now: datetime) -> timedelta:
    """Aldur eldri stuðulsins í parinu."""
    oldest = min(o.quote_a.timestamp.effective, o.quote_b.timestamp.effective)
    return max(now - oldest, timedelta(0))


def format_age(age: timedelta) -> str:
    seconds = int(age.total_seconds())
    if seconds < 60:
        return f"{seconds} sek."
    if seconds < 3600:
        return f"{seconds // 60} mín. {seconds % 60} sek."
    return f"{seconds // 3600} klst. {(seconds % 3600) // 60} mín."


def assess_risk(o: Opportunity, now: datetime, max_odds_age: timedelta = DEFAULT_MAX_QUOTE_AGE) -> RiskAssessment:
    f = o.feasibility
    age = odds_age(o, now)
    reasons = list(f.reasons)

    if o.quote_a.market != o.quote_b.market:
        status = RiskStatus.INCOMPARABLE
        reasons.insert(0, f"Markaðir ekki eins: {o.quote_a.market.label} / {o.quote_b.market.label}")
    elif f.side_a is None or f.side_b is None:
        status = RiskStatus.INCOMPARABLE
        reasons.insert(0, "Fjárhæðir ekki sambærilegar milli veðmálasíðna")
    elif age > max_odds_age:
        status = RiskStatus.STALE
        reasons.insert(0, f"Stuðlar eru {format_age(age)} gamlir (mörk {format_age(max_odds_age)})")
    elif f.status is FeasibilityStatus.STALE_OR_INCOMPARABLE:
        status = RiskStatus.STALE
    elif f.status is FeasibilityStatus.FAILS:
        status = RiskStatus.FAILS
    elif f.status is FeasibilityStatus.UNCONFIRMED:
        status = RiskStatus.UNCONFIRMED
    else:
        status = RiskStatus.MEETS

    # Engar tvíteknar ástæður, röð haldið
    return RiskAssessment(status=status, reasons=tuple(dict.fromkeys(reasons)), odds_age=age)
