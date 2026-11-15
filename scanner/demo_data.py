"""DEMO-gagnasett: tilbúnir leikir og stuðlar. ALLT er merkt DEMO og er ekki raunverulegt.

Gagnasettið inniheldur handsmíðaðar sviðsmyndir (sjá SCENARIOS) og almenna leiki
með stuðlum sem reiknaðir eru út frá Poisson-líkani með álagningu og suði.
Sama `now` og `seed` gefa alltaf sömu gögn.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from scanner.models import (
    DEMO_SOURCE,
    Bookmaker,
    BookmakerEvent,
    DataOrigin,
    StakeLimits,
    Market,
    OddsQuote,
    Outcome,
    Period,
    Timestamp,
    TotalType,
    btts,
    over_under,
)
from scanner.teams import Team, TeamRegistry, default_registry

D = Decimal

BOOKMAKER_NAMES = {"coolbet": "Coolbet", "bet365": "bet365", "epicbet": "Epicbet"}

# DEMO-gengi (ISK fyrir 1 einingu). Ekki raunverulegt gengi.
DEMO_FX_RATES: dict[str, Decimal] = {"EUR": D("150.00"), "USD": D("138.00")}


def demo_bookmakers(now: datetime) -> dict[str, Bookmaker]:
    """DEMO-veðmálamörk. Ekki raunveruleg mörk veðmálasíðnanna.

    - Coolbet: öll mörk skráð, ISK, skref 1 kr.
    - bet365: reikningur í EUR, hámarksveðmál UNKNOWN
    - Epicbet: ISK, skref 10 kr., hámark 60.000 kr.
    """
    return {
        "coolbet": Bookmaker(id="coolbet", name="Coolbet", limits=StakeLimits(
            source=DataOrigin.DEMO, currency="ISK", stake_step=D(1),
            min_stake=D(100), max_stake=D(300_000), available_balance=D(200_000),
            commission_pct=D(0), fixed_fee=D(0), last_confirmed_at=now - timedelta(minutes=20),
        )),
        "bet365": Bookmaker(id="bet365", name="bet365", limits=StakeLimits(
            source=DataOrigin.DEMO, currency="EUR", stake_step=D("0.01"),
            min_stake=D("0.50"), max_stake=None, available_balance=D(1_000),
            commission_pct=D(0), fixed_fee=D(0), last_confirmed_at=now - timedelta(hours=2),
        )),
        "epicbet": Bookmaker(id="epicbet", name="Epicbet", limits=StakeLimits(
            source=DataOrigin.DEMO, currency="ISK", stake_step=D(10),
            min_stake=D(200), max_stake=D(60_000), available_balance=D(100_000),
            commission_pct=D(0), fixed_fee=D(0), last_confirmed_at=now - timedelta(minutes=45),
        )),
    }
BOOKMAKER_MARGIN: dict[str, float] = {"coolbet": 0.050, "bet365": 0.060, "epicbet": 0.045}
PRICE_NOISE = 0.012

OU15, OU25, OU35, BTTS = over_under("1.5"), over_under("2.5"), over_under("3.5"), btts()
SUPPORTED = (OU15, OU25, OU35, BTTS)

O, U, Y, N = Outcome.OVER, Outcome.UNDER, Outcome.YES, Outcome.NO


# ---------------------------------------------------------------------------
# Verðlagning
# ---------------------------------------------------------------------------

def _poisson_cdf(k: int, lam: float) -> float:
    return sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k + 1))


def _fair_probabilities(lam_home: float, lam_away: float) -> dict[tuple[Market, Outcome], float]:
    lam = lam_home + lam_away
    probs = {}
    for market, k in ((OU15, 1), (OU25, 2), (OU35, 3)):
        under = _poisson_cdf(k, lam)
        probs[(market, U)] = under
        probs[(market, O)] = 1 - under
    yes = (1 - math.exp(-lam_home)) * (1 - math.exp(-lam_away))
    probs[(BTTS, Y)] = yes
    probs[(BTTS, N)] = 1 - yes
    return probs


def _price(prob: float, margin: float, noise: float) -> Decimal:
    raw = 1 / (prob * (1 + margin)) * (1 + noise)
    return max(D("1.01"), D(str(raw)).quantize(D("0.01"), rounding=ROUND_DOWN))


# ---------------------------------------------------------------------------
# Byggingareiningar
# ---------------------------------------------------------------------------

@dataclass
class _Quote:
    market: Market
    outcome: Outcome
    price: Decimal
    age_s: int


@dataclass
class _BookEvent:
    bookmaker_id: str
    event_id: str
    competition: str
    home: str
    away: str
    home_code: str | None
    away_code: str | None
    kickoff: datetime
    quotes: list[_Quote] = field(default_factory=list)

    def set_market(self, market: Market, a: str, b: str, age_a: int | None = None, age_b: int | None = None) -> None:
        """Setur báða valkosti markaðar handvirkt (yfirskrifar slembna stuðla)."""
        out_a, out_b = (Y, N) if market == BTTS else (O, U)
        self.quotes = [q for q in self.quotes if q.market != market]
        # Handsettir stuðlar eru nýir (30 sek.) nema annað sé tekið fram
        self.quotes.append(_Quote(market, out_a, D(a), 30 if age_a is None else age_a))
        self.quotes.append(_Quote(market, out_b, D(b), 30 if age_b is None else age_b))

    def build(self, now: datetime) -> BookmakerEvent:
        return BookmakerEvent(
            bookmaker_id=self.bookmaker_id,
            event_id=self.event_id,
            competition=self.competition,
            home_team=self.home,
            away_team=self.away,
            home_code=self.home_code,
            away_code=self.away_code,
            kickoff_utc=self.kickoff,
            source=DEMO_SOURCE,
            quotes=tuple(
                OddsQuote(
                    bookmaker_id=self.bookmaker_id,
                    market=q.market,
                    outcome=q.outcome,
                    price=q.price,
                    timestamp=Timestamp(captured_at=now - timedelta(seconds=q.age_s)),
                    source=DEMO_SOURCE,
                )
                for q in self.quotes
            ),
        )


def _names(registry: TeamRegistry, bookmaker_id: str, comp_id: str, home: Team, away: Team):
    """Hver veðmálasíða notar sinn eigin nafnastíl – eins og í raunveruleikanum."""
    comp = registry.competitions[comp_id]
    if bookmaker_id == "coolbet":  # staðlað heiti + liðakóðar
        return comp.name, home.name, away.name, home.code, away.code
    if bookmaker_id == "bet365":  # fyrsta alias, engir kóðar
        return (
            comp.aliases[0] if comp.aliases else comp.name,
            home.aliases[0] if home.aliases else home.name,
            away.aliases[0] if away.aliases else away.name,
            None, None,
        )
    # epicbet: síðasta alias, engir kóðar
    return (
        comp.aliases[1] if len(comp.aliases) > 1 else comp.name,
        home.aliases[-1] if home.aliases else home.name,
        away.aliases[-1] if away.aliases else away.name,
        None, None,
    )


@dataclass
class _Fixture:
    idx: int
    comp_id: str
    home: Team
    away: Team
    kickoff: datetime
    books: dict[str, _BookEvent]
    extra: list[_BookEvent] = field(default_factory=list)
    scenario: str = "Almennur leikur"


def _make_fixture(
    idx: int, registry: TeamRegistry, home_id: str, away_id: str, kickoff: datetime, rng: random.Random
) -> _Fixture:
    home, away = registry.teams[home_id], registry.teams[away_id]
    comp_id = home.competition_id
    lam_h, lam_a = rng.uniform(1.0, 2.0), rng.uniform(0.7, 1.6)
    probs = _fair_probabilities(lam_h, lam_a)
    books = {}
    for bm, margin in BOOKMAKER_MARGIN.items():
        comp, h, a, hc, ac = _names(registry, bm, comp_id, home, away)
        ev = _BookEvent(bm, f"{bm}-{idx}", comp, h, a, hc, ac, kickoff)
        for market in SUPPORTED:
            if bm == "epicbet" and market == OU35 and rng.random() < 0.15:
                continue  # ekki allar síður bjóða alla markaði
            for outcome in ((Y, N) if market == BTTS else (O, U)):
                age = rng.randint(5, 90)
                if rng.random() < 0.04:
                    age = rng.randint(600, 3600)  # stöku úreltir stuðlar
                price = _price(probs[(market, outcome)], margin, rng.gauss(0, PRICE_NOISE))
                ev.quotes.append(_Quote(market, outcome, price, age))
        if rng.random() < 0.10:  # stöku Asian-línur sem á að útiloka
            ev.quotes.append(_Quote(over_under("2.25", total_type=TotalType.ASIAN), O, D("1.95"), 30))
            ev.quotes.append(_Quote(over_under("2.25", total_type=TotalType.ASIAN), U, D("1.90"), 30))
        books[bm] = ev
    return _Fixture(idx, comp_id, home, away, kickoff, books)


# ---------------------------------------------------------------------------
# Sviðsmyndir
# ---------------------------------------------------------------------------

def _s_alias_arb_2(f: _Fixture, now: datetime) -> None:
    f.scenario = "Ólík liðsheiti + arbitrage ~2,5%"
    f.books["epicbet"].home = "Manchester U."
    f.books["coolbet"].set_market(OU25, "2.08", "1.72")
    f.books["bet365"].set_market(OU25, "1.95", "2.02")
    f.books["epicbet"].set_market(OU25, "2.00", "1.80")


def _s_btts_arb_3(f: _Fixture, now: datetime) -> None:
    f.scenario = "BTTS arbitrage ~3,4%"
    f.books["coolbet"].set_market(BTTS, "1.85", "1.95")
    f.books["bet365"].set_market(BTTS, "1.90", "1.88")
    f.books["epicbet"].set_market(BTTS, "2.20", "1.62")


def _s_ou15_arb_6(f: _Fixture, now: datetime) -> None:
    f.scenario = "Over/Under 1.5 arbitrage ~6%"
    f.books["coolbet"].set_market(OU15, "1.47", "2.70")
    f.books["bet365"].set_market(OU15, "1.36", "3.00")
    f.books["epicbet"].set_market(OU15, "1.30", "3.80")


def _s_stale(f: _Fixture, now: datetime) -> None:
    f.scenario = "Úreltur stuðull sem myndi búa til falska arbitrage"
    f.books["coolbet"].set_market(OU35, "3.30", "1.30")
    f.books["bet365"].set_market(OU35, "6.50", "1.28", age_a=2400)  # 40 mín. gamall
    f.books["epicbet"].set_market(OU35, "3.25", "1.33")


def _s_wrong_lines(f: _Fixture, now: datetime) -> None:
    f.scenario = "Rangar/óstuddar markaðslínur"
    asian = over_under("2.25", total_type=TotalType.ASIAN)
    first_half = over_under("1.5", period=Period.FIRST_HALF)
    f.books["coolbet"].quotes += [_Quote(asian, O, D("1.90"), 20), _Quote(asian, U, D("2.30"), 20)]
    f.books["bet365"].quotes += [_Quote(first_half, O, D("3.40"), 20), _Quote(first_half, U, D("1.30"), 20)]
    f.books["epicbet"].quotes += [
        _Quote(over_under("4.5"), O, D("6.00"), 20),
        _Quote(over_under("2.5", period=Period.INCLUDING_EXTRA_TIME), U, D("2.10"), 20),
    ]


def _s_fuzzy(f: _Fixture, now: datetime) -> None:
    f.scenario = "Innsláttarvilla í liðsheiti (fuzzy) + arbitrage ~2,2%"
    f.books["epicbet"].home = "Tottenham Hotspurs"
    f.books["coolbet"].set_market(BTTS, "2.05", "1.75")
    f.books["bet365"].set_market(BTTS, "1.80", "1.95")
    f.books["epicbet"].set_market(BTTS, "1.85", "2.04")


def _s_uncertain_man(f: _Fixture, now: datetime) -> None:
    f.scenario = "Óviss leikjasamsvörun ('Man')"
    f.books["epicbet"].home = "Man"
    f.books["epicbet"].set_market(OU25, "2.60", "1.50")


def _s_uncertain_real(f: _Fixture, now: datetime) -> None:
    f.scenario = "Óviss leikjasamsvörun ('Real')"
    f.books["bet365"].home = "Real"


def _s_kickoff_mismatch(f: _Fixture, now: datetime) -> None:
    f.scenario = "Ólíkur upphafstími hjá einni síðu"
    f.books["epicbet"].kickoff = f.kickoff + timedelta(hours=1)
    f.books["coolbet"].set_market(OU25, "1.85", "1.95")
    f.books["bet365"].set_market(OU25, "1.88", "1.92")
    f.books["epicbet"].set_market(OU25, "2.40", "1.55")


def _s_duplicates(f: _Fixture, now: datetime) -> None:
    f.scenario = "Tvítekin gögn (eldri stuðull hærri)"
    f.books["coolbet"].set_market(OU25, "1.80", "1.90", age_a=20)
    f.books["bet365"].set_market(OU25, "1.85", "1.95")
    f.books["epicbet"].set_market(OU25, "1.82", "1.98")
    src = f.books["coolbet"]
    dup = _BookEvent(src.bookmaker_id, src.event_id + "-dup", src.competition, src.home, src.away,
                     src.home_code, src.away_code, src.kickoff, [ _Quote(q.market, q.outcome, q.price, q.age_s) for q in src.quotes ])
    dup.set_market(OU25, "2.60", "1.90", age_a=200)  # eldri, hærri stuðull
    f.extra.append(dup)


def _s_not_simultaneous(f: _Fixture, now: datetime) -> None:
    f.scenario = "Arbitrage ~3,7% en stuðlar ekki samtímis"
    f.books["coolbet"].set_market(OU25, "2.12", "1.70", age_a=10)
    f.books["bet365"].set_market(OU25, "1.90", "1.85")
    f.books["epicbet"].set_market(OU25, "1.95", "2.03", age_b=250)


def _s_same_bookmaker(f: _Fixture, now: datetime) -> None:
    f.scenario = "Besti stuðull beggja valkosta hjá sömu síðu"
    f.books["coolbet"].set_market(OU25, "2.20", "2.00")
    f.books["bet365"].set_market(OU25, "1.90", "1.85")
    f.books["epicbet"].set_market(OU25, "1.88", "1.90")


def _s_started(f: _Fixture, now: datetime) -> None:
    f.scenario = "Leikur hafinn (ekki pre-match)"
    started = now - timedelta(minutes=30)
    f.kickoff = started
    for ev in f.books.values():
        ev.kickoff = started


def _s_ou35_arb_6(f: _Fixture, now: datetime) -> None:
    f.scenario = "Over/Under 3.5 arbitrage ~6,5%"
    f.books["coolbet"].set_market(OU35, "3.10", "1.55")
    f.books["bet365"].set_market(OU35, "3.40", "1.38")
    f.books["epicbet"].set_market(OU35, "3.05", "1.40")


def _s_over_max(f: _Fixture, now: datetime) -> None:
    f.scenario = "Arbitrage ~2,4% en fjárhæð yfir hámarksveðmáli"
    f.books["coolbet"].set_market(OU15, "1.28", "4.15")
    f.books["bet365"].set_market(OU15, "1.30", "3.60")
    f.books["epicbet"].set_market(OU15, "1.36", "3.50")


def _s_executable_2(f: _Fixture, now: datetime) -> None:
    f.scenario = "Arbitrage ~2,5%, öll mörk þekkt"
    f.books["coolbet"].set_market(OU25, "2.06", "1.80")
    f.books["bet365"].set_market(OU25, "1.95", "1.86")
    f.books["epicbet"].set_market(OU25, "1.92", "2.04")


SCENARIOS = [
    ("MUN", "LIV", _s_alias_arb_2),
    ("RMA", "ATM", _s_btts_arb_3),
    ("BAY", "BVB", _s_ou15_arb_6),
    ("INT", "JUV", _s_stale),
    ("ARS", "CHE", _s_wrong_lines),
    ("TOT", "NEW", _s_fuzzy),
    ("MCI", "AVL", _s_uncertain_man),
    ("BET", "RSO", _s_uncertain_real),
    ("KR", "VAL", _s_kickoff_mismatch),
    ("BRE", "VIK", _s_duplicates),
    ("NAP", "ROM", _s_not_simultaneous),
    ("RBL", "B04", _s_same_bookmaker),
    ("FH", "STJ", _s_started),
    ("SGE", "VFB", _s_ou35_arb_6),
    ("LAZ", "ATA", _s_over_max),
    ("VIL", "VCF", _s_executable_2),
]


# ---------------------------------------------------------------------------
# Opinbert viðmót
# ---------------------------------------------------------------------------

def _round_kickoff(t: datetime) -> datetime:
    t = t.replace(second=0, microsecond=0)
    return t - timedelta(minutes=t.minute % 15)


def _round_robin(team_ids: list[str], rounds: int) -> list[tuple[str, str]]:
    ids = list(team_ids)
    if len(ids) % 2:
        ids.append("")
    pairs = []
    for r in range(rounds):
        rest = ids[1:]
        rest = rest[-r:] + rest[:-r] if r else rest
        arr = [ids[0], *rest]
        n = len(arr)
        for i in range(n // 2):
            h, a = arr[i], arr[n - 1 - i]
            if h and a:
                pairs.append((h, a) if r % 2 == 0 else (a, h))
    return pairs


def default_anchor(now: datetime) -> datetime:
    """Upphaf morgundagsins (UTC) – grunnur upphafstíma DEMO-leikja."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def build_demo_fixtures(
    now: datetime,
    seed: int = 42,
    total_matches: int = 60,
    registry: TeamRegistry | None = None,
    anchor: datetime | None = None,
) -> list[_Fixture]:
    registry = registry or default_registry()
    rng = random.Random(seed)
    fixtures: list[_Fixture] = []
    used: set[frozenset[str]] = set()

    # Upphafstímar miðast við fast akkeri (sjálfgefið morgundagurinn, UTC), ekki
    # nákvæmlega "núna", svo leikjaauðkenni haldist óbreytt – annars yrðu til ný
    # "tækifæri" í sögunni við hverja endurræsingu eða uppfærslu.
    anchor = anchor or default_anchor(now)

    for i, (h, a, apply) in enumerate(SCENARIOS):
        kickoff = _round_kickoff(anchor + timedelta(hours=15, minutes=45 * i))
        f = _make_fixture(len(fixtures), registry, h, a, kickoff, rng)
        apply(f, now)
        fixtures.append(f)
        used.add(frozenset((h, a)))

    by_comp: dict[str, list[str]] = {}
    for t in registry.teams.values():
        by_comp.setdefault(t.competition_id, []).append(t.id)
    generic = []
    for comp_pairs in (_round_robin(ids, 4) for ids in by_comp.values()):
        generic.extend(comp_pairs)
    # Blanda keppnum saman í röð
    generic.sort(key=lambda p: (registry.teams[p[0]].competition_id, p))
    rng.shuffle(generic)

    k = 0
    for h, a in generic:
        if len(fixtures) >= total_matches:
            break
        if frozenset((h, a)) in used:
            continue
        used.add(frozenset((h, a)))
        kickoff = _round_kickoff(anchor + timedelta(days=1, hours=3 * k))
        fixtures.append(_make_fixture(len(fixtures), registry, h, a, kickoff, rng))
        k += 1
    return fixtures


def build_demo_events(
    now: datetime, seed: int = 42, total_matches: int = 60, anchor: datetime | None = None
) -> list[BookmakerEvent]:
    """Skilar öllum DEMO-viðburðum (eins og þeir kæmu frá veðmálasíðunum).

    Sama `seed` og `anchor` gefa alltaf sömu leiki og sömu stuðla – óháð `now`.
    """
    events = []
    for f in build_demo_fixtures(now, seed, total_matches, anchor=anchor):
        for ev in [*f.books.values(), *f.extra]:
            events.append(ev.build(now))
    return events
