"""Framkvæmdarhæfnimat: er hægt að leggja heildarfjárhæðina á báða valkosti?

Tekur tillit til hámarks- og lágmarksveðmála, inneignar, veðmálaskrefs (námundun),
gjaldmiðla, þóknunar og fastra gjalda. Allt í Decimal.

Óþekkt gildi (UNKNOWN) eru aldrei áætluð. Til að hægt sé að reikna skiptingu er
óþekkt þóknun/gjald talið 0 og óþekkt skref 1, en niðurstaðan er þá alltaf
merkt Óstaðfest. Óþekkt hámark eða inneign er einfaldlega ekki athugað – og
niðurstaðan getur því aldrei talist uppfylla skilyrði.

Uppfylli tækifæri öll skráð skilyrði þýðir það EKKI að veðmálasíða samþykki veðmálið.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum

from scanner.arbitrage import effective_odds, parse_odds, parse_total_stake
from scanner.formatting import format_isk, format_money
from scanner.models import DataOrigin, StakeLimits

ZERO, ONE, HUNDRED = Decimal(0), Decimal(1), Decimal(100)
BASE_CURRENCY = "ISK"
DEFAULT_MAX_LIMITS_AGE = timedelta(hours=24)
MAX_SEARCH_STEPS = 5000  # hámarksfjöldi króna sem leitin lækkar um vegna námundunar

DISCLAIMER = (
    "Uppfyllir skráð skilyrði er ekki trygging: veðmálasíða getur hafnað eða takmarkað "
    "veðmál og stuðlar geta breyst áður en veðmál er lagt."
)


class FeasibilityStatus(StrEnum):
    MEETS = "Uppfyllir skráð fjárhæðarskilyrði"
    UNCONFIRMED = "Óstaðfest"
    FAILS = "Uppfyllir ekki fjárhæðarskilyrði"
    STALE_OR_INCOMPARABLE = "Úrelt eða ósambærilegt"


@dataclass(frozen=True)
class SidePlan:
    """Veðmál á einn valkost."""

    label: str
    odds: Decimal
    effective_odds: Decimal
    currency: str
    fx_rate: Decimal  # ISK fyrir 1 einingu gjaldmiðils
    step: Decimal
    stake: Decimal  # í gjaldmiðli reiknings, margfeldi af `step`
    stake_isk: Decimal
    fee_isk: Decimal
    payout_isk: Decimal  # útborgun ef valkosturinn vinnur (eftir þóknun)
    limits: StakeLimits

    @property
    def stake_display(self) -> str:
        if self.currency == BASE_CURRENCY:
            return format_isk(self.stake)
        return f"{format_money(self.stake, self.currency)} ({format_isk(self.stake_isk)})"


@dataclass(frozen=True)
class FeasibilityResult:
    requested_total: Decimal
    is_arbitrage: bool  # fræðilega, S < 1 á upprunalegum stuðlum
    theoretical_margin_pct: Decimal
    side_a: SidePlan | None  # None ef ekki hægt að reikna (t.d. vantar gengi)
    side_b: SidePlan | None
    total_cost_isk: Decimal | None
    profit_if_a: Decimal | None  # nettóhagnaður í ISK ef A vinnur
    profit_if_b: Decimal | None
    worst_case_profit: Decimal | None
    real_margin_pct: Decimal | None
    status: FeasibilityStatus
    violations: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    incomparable: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    max_feasible_total: Decimal | None = None
    max_feasible_worst_profit: Decimal | None = None
    data_basis: DataOrigin = DataOrigin.MANUAL

    @property
    def reasons(self) -> tuple[str, ...]:
        return (*self.incomparable, *self.violations, *self.unknowns, *self.notes)

    @property
    def meets_conditions(self) -> bool:
        return self.status is FeasibilityStatus.MEETS


# ---------------------------------------------------------------------------
# Innri útreikningar
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Side:
    label: str
    odds: Decimal
    eff: Decimal
    currency: str
    rate: Decimal
    step: Decimal
    fee: Decimal  # í gjaldmiðli reiknings
    limits: StakeLimits

    @property
    def fee_isk(self) -> Decimal:
        return self.fee * self.rate


@dataclass(frozen=True)
class _Allocation:
    stake_a: Decimal
    stake_b: Decimal
    cost_isk: Decimal
    profit_a: Decimal
    profit_b: Decimal

    @property
    def worst(self) -> Decimal:
        return min(self.profit_a, self.profit_b)


def _grid(amount: Decimal, step: Decimal) -> list[Decimal]:
    """Næstu margfeldi af `step` fyrir neðan og ofan `amount`."""
    units = amount / step
    lo = units.to_integral_value(rounding=ROUND_FLOOR) * step
    hi = units.to_integral_value(rounding=ROUND_CEILING) * step
    return sorted({lo, hi})


def _fill(budget_isk: Decimal, side: _Side) -> Decimal:
    """Hæsta margfeldi af skrefi sem rúmast innan `budget_isk`."""
    return (budget_isk / side.rate / side.step).to_integral_value(rounding=ROUND_FLOOR) * side.step


def _allocate(total: Decimal, a: _Side, b: _Side) -> _Allocation | None:
    """Skiptir `total` ISK á valkostina tvo.

    Fjárhæðir eru margfeldi af veðmálaskrefi í gjaldmiðli hvors reiknings og
    heildarkostnaður fer aldrei yfir `total`. Önnur hliðin er námunduð niður
    eða upp að sínu skrefi og hin fyllir upp í afganginn. Valið er það sem
    nýtir mest af `total` og gefur síðan hæstan lægsta hagnað. Með ISK og
    skrefi 1 er samtalan alltaf nákvæmlega `total`, eins og í áfanga 2.
    """
    s_eff = ONE / a.eff + ONE / b.eff
    ideal_a_isk = total * (ONE / a.eff) / s_eff
    ideal_b_isk = total - ideal_a_isk
    fees_isk = a.fee_isk + b.fee_isk

    candidates = {(sa, _fill(total - sa * a.rate, b)) for sa in _grid(ideal_a_isk / a.rate, a.step)}
    candidates |= {(_fill(total - sb * b.rate, a), sb) for sb in _grid(ideal_b_isk / b.rate, b.step)}

    best: _Allocation | None = None
    best_key = None
    for sa, sb in sorted(candidates):
        if sa <= 0 or sb <= 0:
            continue
        cost = sa * a.rate + sb * b.rate
        if cost > total:
            continue
        alloc = _Allocation(
            sa, sb, cost,
            profit_a=sa * a.eff * a.rate - cost - fees_isk,
            profit_b=sb * b.eff * b.rate - cost - fees_isk,
        )
        lower, upper = _check(alloc, a, b)
        # 1) innan skráðra marka  2) nýtir mest af fjárhæðinni  3) hæsti lægsti hagnaður
        key = (not lower and not upper, alloc.cost_isk, alloc.worst)
        if best_key is None or key > best_key:
            best, best_key = alloc, key
    return best


def _violations(side: _Side, stake: Decimal) -> tuple[list[str], list[str]]:
    """Skilar (brot á neðri mörkum, brot á efri mörkum)."""
    lim, cur, lower, upper = side.limits, side.currency, [], []
    if lim.min_stake is not None and stake < lim.min_stake:
        lower.append(f"{side.label}: {format_money(stake, cur)} er undir lágmarksveðmáli {format_money(lim.min_stake, cur)}")
    if lim.max_stake is not None and stake > lim.max_stake:
        upper.append(f"{side.label}: {format_money(stake, cur)} er yfir hámarksveðmáli {format_money(lim.max_stake, cur)}")
    if lim.available_balance is not None and stake + side.fee > lim.available_balance:
        upper.append(
            f"{side.label}: {format_money(stake + side.fee, cur)} (með gjöldum) er umfram "
            f"tiltæka inneign {format_money(lim.available_balance, cur)}"
        )
    return lower, upper


def _check(alloc: _Allocation, a: _Side, b: _Side) -> tuple[list[str], list[str]]:
    la, ua = _violations(a, alloc.stake_a)
    lb, ub = _violations(b, alloc.stake_b)
    return la + lb, ua + ub


def _upper_cap_isk(side: _Side) -> Decimal | None:
    caps = []
    if side.limits.max_stake is not None:
        caps.append(side.limits.max_stake)
    if side.limits.available_balance is not None:
        caps.append(side.limits.available_balance - side.fee)
    return min(caps) * side.rate if caps else None


def _max_feasible(a: _Side, b: _Side) -> _Allocation | None:
    """Hæsta heildarfjárhæð (ISK) sem uppfyllir öll SKRÁÐ skilyrði og skilar
    jákvæðum lægsta nettóhagnaði, með JAFNAÐRI skiptingu (hagnaður nánast eins í
    báðum úrslitum, frávik aðeins vegna námundunar).

    Ójöfn skipting (meira á þann valkost sem hefur ekkert hámark) gæti leyft enn
    hærri heildarfjárhæð, en þá lækkar lægsti hagnaður og hverfur nánast. Slík
    fjárhæð er því ekki talin. None ef ekkert efra mark er skráð eða ekkert gengur."""
    s_eff = ONE / a.eff + ONE / b.eff
    if s_eff >= ONE:
        return None
    shares = {a.label: (ONE / a.eff) / s_eff, b.label: (ONE / b.eff) / s_eff}
    # Námundun getur hnikað hvorri fjárhæð um eitt skref. Því er byrjað nógu
    # ofarlega til að fjárhæðin sé örugglega of há og síðan lækkað um 1 kr.
    slack = a.step * a.rate + b.step * b.rate
    bounds = [(cap + slack) / shares[s.label] for s in (a, b) if (cap := _upper_cap_isk(s)) is not None]
    if not bounds:
        return None
    total = min(bounds).to_integral_value(rounding=ROUND_FLOOR) + ONE

    for _ in range(MAX_SEARCH_STEPS):
        if total < 2:
            return None
        alloc = _allocate(total, a, b)
        if alloc is not None:
            lower, upper = _check(alloc, a, b)
            if not lower and not upper and alloc.worst > 0:
                return alloc
            if lower or (not upper and alloc.worst <= 0):
                # Lægri fjárhæð lagar hvorki lágmarksbrot né neikvæðan hagnað
                return None
        total -= ONE
    return None


def _prepare_side(
    label: str,
    odds: Decimal,
    limits: StakeLimits,
    fx_rates: Mapping[str, Decimal],
    now: datetime,
    max_limits_age: timedelta,
    unknowns: list[str],
    incomparable: list[str],
    notes: list[str],
) -> _Side | None:
    unknowns.extend(f"{label}: {f} UNKNOWN" for f in limits.unknown_fields())

    if limits.last_confirmed_at is not None:
        age = now - limits.last_confirmed_at
        if age > max_limits_age:
            hours = int(age.total_seconds() // 3600)
            incomparable.append(f"{label}: veðmálamörk síðast staðfest fyrir {hours} klst. – úrelt")

    currency = limits.currency or BASE_CURRENCY
    if currency == BASE_CURRENCY:
        rate = ONE
    else:
        rate = fx_rates.get(currency)
        if rate is None or rate <= 0:
            incomparable.append(f"{label}: ekkert gengi skráð fyrir {currency} – ekki sambærilegt við ISK")
            return None
        notes.append(f"{label}: gengi 1 {currency} = {format_isk(rate, 2)} – gengisáhætta ekki metin")

    commission = limits.commission_pct if limits.commission_pct is not None else ZERO
    return _Side(
        label=label,
        odds=odds,
        eff=effective_odds(odds, commission),
        currency=currency,
        rate=rate,
        step=limits.stake_step or ONE,
        fee=limits.fixed_fee or ZERO,
        limits=limits,
    )


def _plan(side: _Side, stake: Decimal) -> SidePlan:
    return SidePlan(
        label=side.label,
        odds=side.odds,
        effective_odds=side.eff,
        currency=side.currency,
        fx_rate=side.rate,
        step=side.step,
        stake=stake,
        stake_isk=stake * side.rate,
        fee_isk=side.fee_isk,
        payout_isk=stake * side.eff * side.rate,
        limits=side.limits,
    )


# ---------------------------------------------------------------------------
# Opinbert viðmót
# ---------------------------------------------------------------------------

def assess_feasibility(
    odds_a: Decimal | str | float,
    odds_b: Decimal | str | float,
    limits_a: StakeLimits,
    limits_b: StakeLimits,
    total_isk: Decimal | int | str = 100_000,
    *,
    now: datetime,
    fx_rates: Mapping[str, Decimal] | None = None,
    max_limits_age: timedelta = DEFAULT_MAX_LIMITS_AGE,
    odds_origin: DataOrigin = DataOrigin.MANUAL,
    extra_unconfirmed: tuple[str, ...] = (),
    extra_incomparable: tuple[str, ...] = (),
    label_a: str = "A",
    label_b: str = "B",
) -> FeasibilityResult:
    a_odds = parse_odds(odds_a, "Stuðull A")
    b_odds = parse_odds(odds_b, "Stuðull B")
    total = parse_total_stake(total_isk)
    fx_rates = fx_rates or {}

    s = ONE / a_odds + ONE / b_odds
    is_arb = s < ONE
    margin = (ONE / s - ONE) * HUNDRED
    basis = DataOrigin.weakest(limits_a.source, limits_b.source, odds_origin)

    unknowns: list[str] = []
    incomparable: list[str] = list(extra_incomparable)
    notes: list[str] = []
    if label_a == label_b:
        raise ValueError("Merkingar valkosta verða að vera ólíkar")
    a = _prepare_side(label_a, a_odds, limits_a, fx_rates, now, max_limits_age, unknowns, incomparable, notes)
    b = _prepare_side(label_b, b_odds, limits_b, fx_rates, now, max_limits_age, unknowns, incomparable, notes)
    unknowns.extend(extra_unconfirmed)

    common = dict(
        requested_total=total, is_arbitrage=is_arb, theoretical_margin_pct=margin,
        unknowns=tuple(unknowns), notes=tuple(notes), data_basis=basis,
    )

    if a is None or b is None:
        return FeasibilityResult(
            side_a=None, side_b=None, total_cost_isk=None, profit_if_a=None, profit_if_b=None,
            worst_case_profit=None, real_margin_pct=None,
            status=FeasibilityStatus.STALE_OR_INCOMPARABLE, incomparable=tuple(incomparable), **common,
        )

    alloc = _allocate(total, a, b)
    violations: list[str] = []
    if alloc is None:
        violations.append(f"Ekki hægt að skipta {format_isk(total)} á valkostina með þessum veðmálaskrefum")
    else:
        lower, upper = _check(alloc, a, b)
        violations.extend(lower + upper)
        if alloc.worst <= 0:
            violations.append(
                f"Lægsti nettóhagnaður er {format_isk(alloc.worst, 2)} eftir námundun, gjöld og gjaldmiðla"
            )

    best = _max_feasible(a, b)

    if incomparable:
        status = FeasibilityStatus.STALE_OR_INCOMPARABLE
    elif violations:
        status = FeasibilityStatus.FAILS
    elif unknowns:
        status = FeasibilityStatus.UNCONFIRMED
    else:
        status = FeasibilityStatus.MEETS

    if best is not None and (unknowns or incomparable):
        notes.append("Hæsta mögulega fjárhæð byggir aðeins á skráðum skilyrðum, sem eru ekki öll þekkt eða gild")

    return FeasibilityResult(
        side_a=_plan(a, alloc.stake_a) if alloc else None,
        side_b=_plan(b, alloc.stake_b) if alloc else None,
        total_cost_isk=alloc.cost_isk if alloc else None,
        profit_if_a=alloc.profit_a if alloc else None,
        profit_if_b=alloc.profit_b if alloc else None,
        worst_case_profit=alloc.worst if alloc else None,
        real_margin_pct=alloc.worst / total * HUNDRED if alloc else None,
        status=status,
        violations=tuple(violations),
        incomparable=tuple(incomparable),
        max_feasible_total=best.cost_isk if best else None,
        max_feasible_worst_profit=best.worst if best else None,
        **{**common, "notes": tuple(notes)},
    )
