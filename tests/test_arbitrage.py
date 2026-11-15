from decimal import Decimal

import pytest

from scanner.arbitrage import (
    ArbitrageResult,
    BetLimits,
    ExecutionStatus,
    calculate_arbitrage,
    implied_sum,
    is_arbitrage,
    parse_odds,
    parse_total_stake,
    theoretical_margin_pct,
    to_decimal,
)

D = Decimal
T = 100_000

# Allar upplýsingar þekktar, engin gjöld og rúm mörk
KNOWN = BetLimits(
    commission_pct=0,
    fixed_fee_isk=0,
    min_stake_isk=100,
    max_stake_isk=1_000_000,
    available_balance_isk=1_000_000,
)


def known(**overrides) -> BetLimits:
    values = {
        "commission_pct": 0,
        "fixed_fee_isk": 0,
        "min_stake_isk": 100,
        "max_stake_isk": 1_000_000,
        "available_balance_isk": 1_000_000,
    }
    values.update(overrides)
    return BetLimits(**values)


# ---------------------------------------------------------------------------
# Grunnformúlur
# ---------------------------------------------------------------------------

def test_implied_sum_and_margin_2_10_vs_2_05():
    s = implied_sum(D("2.10"), D("2.05"))
    assert s == D(1) / D("2.10") + D(1) / D("2.05")
    assert s < 1
    assert is_arbitrage(D("2.10"), D("2.05"))
    assert round(theoretical_margin_pct(D("2.10"), D("2.05")), 4) == D("3.7349")


def test_equal_odds_1_90_is_not_arbitrage():
    assert not is_arbitrage(D("1.90"), D("1.90"))
    assert theoretical_margin_pct(D("1.90"), D("1.90")) == D("-5")


def test_exactly_2_00_vs_2_00_is_not_arbitrage():
    # S == 1 nákvæmlega -> ekki arbitrage (krafan er S < 1)
    assert implied_sum(D("2.00"), D("2.00")) == 1
    assert not is_arbitrage(D("2.00"), D("2.00"))


# ---------------------------------------------------------------------------
# Prófunardæmi úr kröfulýsingu
# ---------------------------------------------------------------------------

def test_case_2_10_vs_2_05_at_100k():
    r = calculate_arbitrage("2.10", "2.05", T)
    assert r.is_arbitrage
    assert r.stake_a == D(49398)
    assert r.stake_b == D(50602)
    assert r.stake_a + r.stake_b == D(T)
    assert r.profit_if_a == D("3735.80")  # 49398 × 2.10 − 100000
    assert r.profit_if_b == D("3734.10")  # 50602 × 2.05 − 100000
    assert r.worst_case_profit == D("3734.10")
    assert round(r.real_margin_pct, 4) == D("3.7341")
    assert round(r.theoretical_profit, 2) == D("3734.94")
    # Engar upplýsingar um mörk/gjöld gefnar -> óstaðfest
    assert r.status is ExecutionStatus.UNCONFIRMED


def test_case_1_90_vs_1_90_at_100k():
    r = calculate_arbitrage("1.90", "1.90", T)
    assert not r.is_arbitrage
    assert r.status is ExecutionStatus.NO_ARBITRAGE
    assert r.stake_a == r.stake_b == D(50_000)
    assert r.profit_if_a == r.profit_if_b == D(-5000)
    assert r.worst_case_profit == D(-5000)


def test_case_2_15_vs_2_05_at_100k():
    r = calculate_arbitrage("2.15", "2.05", T, KNOWN, KNOWN)
    assert r.is_arbitrage
    assert round(r.theoretical_margin_pct, 4) == D("4.9405")
    assert (r.stake_a, r.stake_b) == (D(48810), D(51190))
    assert r.profit_if_a == D("4941.50")
    assert r.profit_if_b == D("4939.50")
    assert r.worst_case_profit == D("4939.50")
    assert r.status is ExecutionStatus.EXECUTABLE
    assert r.reasons == ()


def test_default_total_is_100k():
    r = calculate_arbitrage("2.10", "2.05")
    assert r.total_stake == D(100_000)


def test_max_stake_too_low():
    r = calculate_arbitrage("2.10", "2.05", T, known(max_stake_isk=40_000), KNOWN)
    assert r.status is ExecutionStatus.NOT_EXECUTABLE
    assert any("hámarksveðmáli" in x for x in r.reasons)
    # Kerfið stingur upp á hæstu heildarfjárhæð innan marka
    assert r.max_total_within_limits == D(80_975)
    fitted = calculate_arbitrage("2.10", "2.05", r.max_total_within_limits, known(max_stake_isk=40_000), KNOWN)
    assert fitted.stake_a <= 40_000
    assert fitted.status is ExecutionStatus.EXECUTABLE


def test_unknown_max_stake_is_unconfirmed():
    r = calculate_arbitrage("2.10", "2.05", T, known(max_stake_isk=None), KNOWN)
    assert r.status is ExecutionStatus.UNCONFIRMED
    assert "A: hámarksveðmál óþekkt" in r.reasons


def test_rounding_can_erase_small_profit():
    # 2.01 / 2.00: fræðileg framlegð ~0,25% en við 100 kr. étur námundun hagnaðinn
    r = calculate_arbitrage("2.01", "2.00", 100, KNOWN, KNOWN)
    assert r.is_arbitrage
    assert r.theoretical_margin_pct > 0
    assert r.worst_case_profit <= 0
    assert r.real_margin_pct <= 0
    assert r.rounding_cost > 0
    assert r.status is ExecutionStatus.UNPROFITABLE


def test_rounding_can_turn_profit_into_loss():
    r = calculate_arbitrage("3.00", "1.51", 10, known(min_stake_isk=1), known(min_stake_isk=1))
    assert r.is_arbitrage
    assert r.worst_case_profit < 0
    assert r.status is ExecutionStatus.UNPROFITABLE


def test_same_odds_profitable_with_larger_total():
    # Sami stuðull og að ofan, en 100.000 kr. -> hagnaður lifir af námundun
    r = calculate_arbitrage("2.01", "2.00", T, KNOWN, KNOWN)
    assert r.worst_case_profit > 0
    assert r.status is ExecutionStatus.EXECUTABLE


# ---------------------------------------------------------------------------
# Gjöld, lágmark, inneign
# ---------------------------------------------------------------------------

def test_fixed_fees_reduce_profit_exactly():
    base = calculate_arbitrage("2.10", "2.05", T, KNOWN, KNOWN)
    fees = calculate_arbitrage("2.10", "2.05", T, known(fixed_fee_isk=500), known(fixed_fee_isk=300))
    assert (fees.stake_a, fees.stake_b) == (base.stake_a, base.stake_b)
    assert fees.worst_case_profit == base.worst_case_profit - 800


def test_commission_reduces_profit_and_rebalances_stakes():
    base = calculate_arbitrage("2.10", "2.05", T, KNOWN, KNOWN)
    comm = calculate_arbitrage("2.10", "2.05", T, known(commission_pct=5), KNOWN)
    assert comm.worst_case_profit < base.worst_case_profit
    # Þóknun á A lækkar virkan stuðul A -> meira fé á A til að jafna
    assert comm.stake_a > base.stake_a
    assert abs(comm.profit_if_a - comm.profit_if_b) < D("2.10")


def test_commission_can_kill_arbitrage():
    r = calculate_arbitrage("2.10", "2.05", T, known(commission_pct=10), known(commission_pct=10))
    assert r.is_arbitrage  # fræðilega, án gjalda
    assert r.worst_case_profit < 0
    assert r.status is ExecutionStatus.UNPROFITABLE


def test_below_min_stake():
    r = calculate_arbitrage("2.10", "2.05", 100, known(min_stake_isk=100), known(min_stake_isk=100))
    assert r.status is ExecutionStatus.NOT_EXECUTABLE
    assert any("lágmarksveðmáli" in x for x in r.reasons)


def test_insufficient_balance():
    r = calculate_arbitrage("2.10", "2.05", T, KNOWN, known(available_balance_isk=50_000))
    assert r.status is ExecutionStatus.NOT_EXECUTABLE
    assert any("inneign" in x for x in r.reasons)
    assert r.max_total_within_limits is not None
    assert r.max_total_within_limits < T


def test_balance_must_cover_fixed_fee():
    # Fjárhæð B er 50.602 kr.; inneign 50.602 dugar ekki ef 10 kr. gjald bætist við
    r = calculate_arbitrage(
        "2.10", "2.05", T, KNOWN, known(available_balance_isk=50_602, fixed_fee_isk=10)
    )
    assert r.status is ExecutionStatus.NOT_EXECUTABLE


def test_all_missing_info_listed():
    r = calculate_arbitrage("2.10", "2.05", T)
    assert len([x for x in r.reasons if "óþekkt" in x]) == 10


# ---------------------------------------------------------------------------
# Almennir eiginleikar
# ---------------------------------------------------------------------------

ODDS_PAIRS = [
    ("2.10", "2.05"),
    ("2.15", "2.05"),
    ("1.90", "1.90"),
    ("1.50", "3.20"),
    ("1.01", "101"),
    ("3.75", "1.37"),
    ("2.01", "2.00"),
    ("1.83", "2.24"),
]


@pytest.mark.parametrize("a,b", ODDS_PAIRS)
@pytest.mark.parametrize("total", [2, 10, 999, 100_000, 1_234_567])
def test_invariants(a, b, total):
    r = calculate_arbitrage(a, b, total)
    assert r.stake_a + r.stake_b == D(total)
    assert r.stake_a == r.stake_a.to_integral_value()
    assert r.stake_b == r.stake_b.to_integral_value()
    assert r.stake_a >= 1 and r.stake_b >= 1
    assert r.worst_case_profit == min(r.profit_if_a, r.profit_if_b)
    # Námundun getur aldrei gefið meira en fræðilegur hagnaður
    assert r.worst_case_profit <= r.theoretical_profit
    assert r.rounding_cost >= 0
    # Námundun um ≤1 kr. kostar í mesta lagi hæsta stuðulinn
    assert r.rounding_cost <= max(D(a), D(b))


def test_all_money_values_are_decimal():
    r = calculate_arbitrage("2.10", "2.05", T, KNOWN, KNOWN)
    for name in ArbitrageResult.__dataclass_fields__:
        value = getattr(r, name)
        if name in {"is_arbitrage", "status", "reasons", "max_total_within_limits"}:
            continue
        assert isinstance(value, Decimal), name


# ---------------------------------------------------------------------------
# Innsláttur
# ---------------------------------------------------------------------------

def test_comma_decimal_input():
    assert parse_odds("2,10") == D("2.10")


def test_float_input_is_exact():
    assert to_decimal(2.1) == D("2.1")


@pytest.mark.parametrize("bad", ["1.00", "0.5", "-2", "abc", "", "nan", "inf"])
def test_invalid_odds_rejected(bad):
    with pytest.raises(ValueError):
        parse_odds(bad)


@pytest.mark.parametrize("bad", ["100.5", "1", "0", "-100", "abc"])
def test_invalid_total_rejected(bad):
    with pytest.raises(ValueError):
        parse_total_stake(bad)


def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        BetLimits(min_stake_isk=1000, max_stake_isk=500)
    with pytest.raises(ValueError):
        BetLimits(commission_pct=100)
    with pytest.raises(ValueError):
        BetLimits(fixed_fee_isk=-1)
