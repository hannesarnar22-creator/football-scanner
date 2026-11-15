"""Arbitrage-reiknivél fyrir tvo andstæða valkosti (t.d. Over/Under eða BTTS Já/Nei).

Allir fjárhagsútreikningar nota Decimal. Engin float-tala fer inn í útreikninga.

Formúlur (án gjalda):
    S                  = 1/A + 1/B          (arbitrage ef S < 1)
    Fræðileg framlegð  = (1/S - 1) × 100
    Stake A            = T × (1/A) / S
    Stake B            = T × (1/B) / S

Gjöld:
    - commission_pct: þóknun (%) af nettóvinningi þess veðmáls sem vinnur.
      Skiptingin notar þá virkan stuðul  A' = 1 + (A - 1) × (1 - c)
      svo hagnaðurinn verði sem jafnastur. Án þóknunar er A' = A.
    - fixed_fee_isk: föst krónutala á hvert veðmál, dregin frá í báðum útkomum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from enum import Enum

from scanner.formatting import format_isk

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


# ---------------------------------------------------------------------------
# Innsláttur
# ---------------------------------------------------------------------------

def to_decimal(value: Decimal | int | float | str, name: str = "gildi") -> Decimal:
    """Breytir innslætti í Decimal. Tekur við kommu sem tugabrotskilum ('2,10')."""
    if isinstance(value, bool):
        raise TypeError(f"{name}: bool er ekki leyfilegt")
    try:
        if isinstance(value, Decimal):
            result = value
        elif isinstance(value, int):
            result = Decimal(value)
        elif isinstance(value, float):
            # repr gefur stystu nákvæmu framsetningu (2.1 -> '2.1', ekki 2.0999…)
            result = Decimal(repr(value))
        elif isinstance(value, str):
            result = Decimal(value.strip().replace(",", "."))
        else:
            raise TypeError(f"{name}: óstudd tegund {type(value).__name__}")
    except InvalidOperation as exc:
        raise ValueError(f"{name}: '{value}' er ekki gild tala") from exc
    if not result.is_finite():
        raise ValueError(f"{name}: verður að vera endanleg tala")
    return result


def parse_odds(value: Decimal | int | float | str, name: str = "Stuðull") -> Decimal:
    odds = to_decimal(value, name)
    if odds <= ONE:
        raise ValueError(f"{name} verður að vera hærri en 1,00 (fékk {odds})")
    return odds


def parse_total_stake(value: Decimal | int | float | str) -> Decimal:
    total = to_decimal(value, "Heildarfjárhæð")
    if total != total.to_integral_value():
        raise ValueError("Heildarfjárhæð verður að vera heil krónutala")
    if total < 2:
        raise ValueError("Heildarfjárhæð verður að vera a.m.k. 2 kr.")
    return total


# ---------------------------------------------------------------------------
# Grunnformúlur
# ---------------------------------------------------------------------------

def implied_sum(odds_a: Decimal, odds_b: Decimal) -> Decimal:
    """S = 1/A + 1/B"""
    return ONE / odds_a + ONE / odds_b


def is_arbitrage(odds_a: Decimal, odds_b: Decimal) -> bool:
    return implied_sum(odds_a, odds_b) < ONE


def theoretical_margin_pct(odds_a: Decimal, odds_b: Decimal) -> Decimal:
    """(1/S - 1) × 100"""
    return (ONE / implied_sum(odds_a, odds_b) - ONE) * HUNDRED


def effective_odds(odds: Decimal, commission_pct: Decimal) -> Decimal:
    """Stuðull eftir þóknun af nettóvinningi."""
    return ONE + (odds - ONE) * (ONE - commission_pct / HUNDRED)


# ---------------------------------------------------------------------------
# Takmarkanir og gjöld hjá veðmálasíðu
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BetLimits:
    """Gjöld og takmarkanir fyrir eitt veðmál. None þýðir óþekkt gildi."""

    commission_pct: Decimal | None = None
    fixed_fee_isk: Decimal | None = None
    min_stake_isk: Decimal | None = None
    max_stake_isk: Decimal | None = None
    available_balance_isk: Decimal | None = None

    FIELD_LABELS = {
        "commission_pct": "þóknun",
        "fixed_fee_isk": "föst gjöld",
        "min_stake_isk": "lágmarksveðmál",
        "max_stake_isk": "hámarksveðmál",
        "available_balance_isk": "tiltæk inneign",
    }

    def __post_init__(self) -> None:
        for name in self.FIELD_LABELS:
            raw = getattr(self, name)
            if raw is None:
                continue
            value = to_decimal(raw, name)
            if value < ZERO:
                raise ValueError(f"{self.FIELD_LABELS[name]} má ekki vera neikvæð")
            object.__setattr__(self, name, value)
        if self.commission_pct is not None and self.commission_pct >= HUNDRED:
            raise ValueError("þóknun verður að vera undir 100%")
        if (
            self.min_stake_isk is not None
            and self.max_stake_isk is not None
            and self.min_stake_isk > self.max_stake_isk
        ):
            raise ValueError("lágmarksveðmál má ekki vera hærra en hámarksveðmál")

    def missing_fields(self) -> list[str]:
        return [label for name, label in self.FIELD_LABELS.items() if getattr(self, name) is None]

    @property
    def commission(self) -> Decimal:
        return self.commission_pct or ZERO

    @property
    def fixed_fee(self) -> Decimal:
        return self.fixed_fee_isk or ZERO



# ---------------------------------------------------------------------------
# Niðurstaða
# ---------------------------------------------------------------------------

class ExecutionStatus(Enum):
    EXECUTABLE = "Framkvæmanlegt"
    UNCONFIRMED = "Óstaðfest"
    NOT_EXECUTABLE = "Ekki framkvæmanlegt"
    UNPROFITABLE = "Enginn hagnaður eftir námundun/gjöld"
    NO_ARBITRAGE = "Ekki arbitrage"


@dataclass(frozen=True)
class ArbitrageResult:
    odds_a: Decimal
    odds_b: Decimal
    total_stake: Decimal
    implied_sum: Decimal
    is_arbitrage: bool
    theoretical_margin_pct: Decimal
    theoretical_profit: Decimal  # T × (1/S - 1), án námundunar og gjalda
    raw_stake_a: Decimal  # ónámunduð fjárhæð
    raw_stake_b: Decimal
    stake_a: Decimal  # heilar krónur, stake_a + stake_b == total_stake
    stake_b: Decimal
    profit_if_a: Decimal  # nettóhagnaður ef A vinnur (eftir gjöld)
    profit_if_b: Decimal
    worst_case_profit: Decimal
    real_margin_pct: Decimal  # worst_case_profit / T × 100
    rounding_cost: Decimal  # hagnaður sem tapast við námundun í heilar krónur
    status: ExecutionStatus
    reasons: tuple[str, ...] = field(default_factory=tuple)
    max_total_within_limits: Decimal | None = None


# ---------------------------------------------------------------------------
# Útreikningur
# ---------------------------------------------------------------------------

def _profits(
    stake_a: Decimal,
    stake_b: Decimal,
    eff_a: Decimal,
    eff_b: Decimal,
    total: Decimal,
    fixed_total: Decimal,
) -> tuple[Decimal, Decimal]:
    return (
        stake_a * eff_a - total - fixed_total,
        stake_b * eff_b - total - fixed_total,
    )


def _split(
    total: Decimal, eff_a: Decimal, eff_b: Decimal, fixed_total: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Skiptir T í heilar krónur þannig að samtalan sé nákvæmlega T.

    Prófar bæði að námunda A niður og upp (B = T - A) og velur þann kost sem
    gefur hæstan lægsta hagnað.
    """
    s_eff = ONE / eff_a + ONE / eff_b
    raw_a = total * (ONE / eff_a) / s_eff
    candidates = {
        raw_a.to_integral_value(rounding=ROUND_FLOOR),
        raw_a.to_integral_value(rounding=ROUND_CEILING),
    }
    best: tuple[Decimal, Decimal, Decimal, Decimal] | None = None
    for a in sorted(candidates):
        a = min(max(a, ONE), total - ONE).quantize(ONE)  # bæði veðmál a.m.k. 1 kr.
        b = (total - a).quantize(ONE)
        pa, pb = _profits(a, b, eff_a, eff_b, total, fixed_total)
        if best is None or min(pa, pb) > min(best[2], best[3]):
            best = (a, b, pa, pb)
    assert best is not None
    return best


def _upper_limit(limits: BetLimits) -> Decimal | None:
    """Hæsta fjárhæð sem má setja á þennan valkost (hámark og inneign)."""
    caps = []
    if limits.max_stake_isk is not None:
        caps.append(limits.max_stake_isk)
    if limits.available_balance_isk is not None:
        caps.append(limits.available_balance_isk - limits.fixed_fee)
    return min(caps) if caps else None


def _violations(stake: Decimal, limits: BetLimits, side: str) -> list[str]:
    out = []
    if limits.min_stake_isk is not None and stake < limits.min_stake_isk:
        out.append(
            f"{side}: fjárhæð {format_isk(stake)} er undir lágmarksveðmáli {format_isk(limits.min_stake_isk)}"
        )
    if limits.max_stake_isk is not None and stake > limits.max_stake_isk:
        out.append(
            f"{side}: fjárhæð {format_isk(stake)} er yfir hámarksveðmáli {format_isk(limits.max_stake_isk)}"
        )
    if limits.available_balance_isk is not None and stake + limits.fixed_fee > limits.available_balance_isk:
        out.append(
            f"{side}: fjárhæð og gjöld ({format_isk(stake + limits.fixed_fee, 2)}) eru umfram "
            f"tiltæka inneign {format_isk(limits.available_balance_isk)}"
        )
    return out


def _max_total_within_limits(
    eff_a: Decimal,
    eff_b: Decimal,
    limits_a: BetLimits,
    limits_b: BetLimits,
    fixed_total: Decimal,
) -> Decimal | None:
    """Hæsta heildarfjárhæð þar sem hvorugt veðmál fer yfir hámark eða inneign."""
    cap_a, cap_b = _upper_limit(limits_a), _upper_limit(limits_b)
    s_eff = ONE / eff_a + ONE / eff_b
    share_a = (ONE / eff_a) / s_eff
    share_b = (ONE / eff_b) / s_eff
    bounds = []
    if cap_a is not None:
        bounds.append(cap_a / share_a)
    if cap_b is not None:
        bounds.append(cap_b / share_b)
    if not bounds:
        return None
    total = min(bounds).to_integral_value(rounding=ROUND_FLOOR)
    # Námundun getur hnikað veðmáli um 1 kr. yfir mörkin – lækka þar til það passar
    while total >= 2:
        a, b, _, _ = _split(total, eff_a, eff_b, fixed_total)
        if (cap_a is None or a <= cap_a) and (cap_b is None or b <= cap_b):
            return total
        total -= ONE
    return None


def calculate_arbitrage(
    odds_a: Decimal | int | float | str,
    odds_b: Decimal | int | float | str,
    total_stake: Decimal | int | float | str = 100_000,
    limits_a: BetLimits | None = None,
    limits_b: BetLimits | None = None,
) -> ArbitrageResult:
    """Reiknar arbitrage, veðmálaskiptingu, hagnað og framkvæmdarhæfni."""
    a = parse_odds(odds_a, "Stuðull A")
    b = parse_odds(odds_b, "Stuðull B")
    total = parse_total_stake(total_stake)
    limits_a = limits_a or BetLimits()
    limits_b = limits_b or BetLimits()

    s = implied_sum(a, b)
    arb = s < ONE
    margin = (ONE / s - ONE) * HUNDRED
    theoretical_profit = total * (ONE / s - ONE)
    raw_a = total * (ONE / a) / s
    raw_b = total * (ONE / b) / s

    eff_a = effective_odds(a, limits_a.commission)
    eff_b = effective_odds(b, limits_b.commission)
    fixed_total = limits_a.fixed_fee + limits_b.fixed_fee

    stake_a, stake_b, profit_a, profit_b = _split(total, eff_a, eff_b, fixed_total)
    worst = min(profit_a, profit_b)
    real_margin = worst / total * HUNDRED

    # Hagnaður eftir gjöld en fyrir námundun (jafn í báðum útkomum)
    unrounded_profit = total / (ONE / eff_a + ONE / eff_b) - total - fixed_total
    rounding_cost = unrounded_profit - worst

    violations = _violations(stake_a, limits_a, "A") + _violations(stake_b, limits_b, "B")
    missing = [f"A: {m} óþekkt" for m in limits_a.missing_fields()] + [
        f"B: {m} óþekkt" for m in limits_b.missing_fields()
    ]

    reasons: list[str] = []
    max_total = None
    if not arb:
        status = ExecutionStatus.NO_ARBITRAGE
        reasons.append(f"S = {s:.6f} ≥ 1 – engin arbitrage")
    elif worst <= ZERO:
        status = ExecutionStatus.UNPROFITABLE
        reasons.append(
            "Fræðileg arbitrage er til staðar en lægsti hagnaður eftir námundun og gjöld er "
            f"{format_isk(worst, 2)}"
        )
    elif violations:
        status = ExecutionStatus.NOT_EXECUTABLE
    elif missing:
        status = ExecutionStatus.UNCONFIRMED
    else:
        status = ExecutionStatus.EXECUTABLE

    reasons.extend(violations)
    if arb:
        reasons.extend(missing)

    if any("hámarksveðmáli" in v or "inneign" in v for v in violations):
        max_total = _max_total_within_limits(eff_a, eff_b, limits_a, limits_b, fixed_total)
        if max_total is not None:
            reasons.append(f"Hæsta heildarfjárhæð innan marka: {format_isk(max_total)}")

    return ArbitrageResult(
        odds_a=a,
        odds_b=b,
        total_stake=total,
        implied_sum=s,
        is_arbitrage=arb,
        theoretical_margin_pct=margin,
        theoretical_profit=theoretical_profit,
        raw_stake_a=raw_a,
        raw_stake_b=raw_b,
        stake_a=stake_a,
        stake_b=stake_b,
        profit_if_a=profit_a,
        profit_if_b=profit_b,
        worst_case_profit=worst,
        real_margin_pct=real_margin,
        rounding_cost=rounding_cost,
        status=status,
        reasons=tuple(reasons),
        max_total_within_limits=max_total,
    )
