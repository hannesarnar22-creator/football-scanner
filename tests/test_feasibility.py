"""Framkvæmdarhæfnimat (áfangi 4)."""

from datetime import timedelta
from decimal import Decimal

import pytest

from scanner.arbitrage import BetLimits, calculate_arbitrage
from scanner.feasibility import DISCLAIMER, FeasibilityStatus, assess_feasibility
from scanner.models import DataOrigin, StakeLimits
from tests.helpers import NOW, limits

D = Decimal
S = FeasibilityStatus
FX = {"EUR": D("150.00"), "USD": D("138.00")}


def assess(a="2.10", b="2.05", la=None, lb=None, total=100_000, **kw):
    kw.setdefault("fx_rates", FX)
    return assess_feasibility(a, b, la or limits(), lb or limits(), total, now=NOW, **kw)


# ---------------------------------------------------------------------------
# Nægileg mörk og réttur hagnaður í báðum úrslitum
# ---------------------------------------------------------------------------

def test_sufficient_limits_meet_conditions():
    f = assess()
    assert f.status is S.MEETS
    assert f.meets_conditions
    assert (f.side_a.stake, f.side_b.stake) == (D(49398), D(50602))
    assert f.total_cost_isk == D(100_000)
    assert f.reasons == ()


def test_profit_in_both_outcomes_exact():
    f = assess()
    # A vinnur: 49.398 × 2,10 − 100.000 ; B vinnur: 50.602 × 2,05 − 100.000
    assert f.profit_if_a == D("3735.80")
    assert f.profit_if_b == D("3734.10")
    assert f.worst_case_profit == D("3734.10")
    assert f.real_margin_pct == D("3.7341")


def test_profit_formula_holds_generally():
    f = assess("2.15", "2.05", limits(fixed_fee=D(50)), limits(commission_pct=D(2)))
    fees = D(50)
    a, b = f.side_a, f.side_b
    assert f.profit_if_a == a.stake * D("2.15") - f.total_cost_isk - fees
    eff_b = 1 + (D("2.05") - 1) * (1 - D("0.02"))
    assert f.profit_if_b == b.stake * eff_b - f.total_cost_isk - fees


@pytest.mark.parametrize(
    "a,b,total",
    [("2.10", "2.05", 100_000), ("2.15", "2.05", 100_000), ("1.47", "3.80", 100_000),
     ("2.01", "2.00", 100), ("3.00", "1.51", 10), ("1.90", "1.90", 100_000), ("2.10", "2.05", 1_234_567)],
)
def test_matches_phase2_calculator_for_isk(a, b, total):
    f = assess(a, b, total=total, la=limits(min_stake=D(1)), lb=limits(min_stake=D(1)))
    p2 = calculate_arbitrage(a, b, total)
    assert (f.side_a.stake, f.side_b.stake) == (p2.stake_a, p2.stake_b)
    assert (f.profit_if_a, f.profit_if_b) == (p2.profit_if_a, p2.profit_if_b)
    assert f.theoretical_margin_pct == p2.theoretical_margin_pct


def test_all_values_are_decimal():
    f = assess(la=limits(currency="EUR", stake_step=D("0.01"), min_stake=D("0.5"),
                         max_stake=D(5000), available_balance=D(5000)))
    for v in (f.total_cost_isk, f.profit_if_a, f.profit_if_b, f.worst_case_profit, f.real_margin_pct,
              f.side_a.stake, f.side_a.stake_isk, f.max_feasible_total):
        assert isinstance(v, Decimal)


# ---------------------------------------------------------------------------
# Hámarksveðmál
# ---------------------------------------------------------------------------

def test_max_stake_too_low():
    f = assess(la=limits(max_stake=D(40_000)))
    assert f.status is S.FAILS
    assert any("hámarksveðmáli" in v for v in f.violations)
    assert not f.meets_conditions


def test_unknown_max_stake_is_unconfirmed():
    f = assess(la=limits(max_stake=None))
    assert f.status is S.UNCONFIRMED
    assert "A: hámarksveðmál UNKNOWN" in f.unknowns
    assert not f.meets_conditions


@pytest.mark.parametrize("field", list(StakeLimits.FIELD_LABELS))
def test_any_unknown_field_prevents_meeting(field):
    f = assess(lb=limits(**{field: None}))
    assert f.status is S.UNCONFIRMED
    assert any("UNKNOWN" in u for u in f.unknowns)


def test_unknown_values_are_not_estimated():
    f = assess(la=limits(max_stake=None, available_balance=None), lb=limits(max_stake=None, available_balance=None))
    # Ekkert efra mark skráð -> engin "hæsta fjárhæð" áætluð
    assert f.max_feasible_total is None


# ---------------------------------------------------------------------------
# Inneign
# ---------------------------------------------------------------------------

def test_insufficient_balance():
    f = assess(lb=limits(available_balance=D(30_000)))
    assert f.status is S.FAILS
    assert any("inneign" in v for v in f.violations)


def test_balance_must_cover_fixed_fee():
    ok = assess(lb=limits(available_balance=D(50_602)))
    assert ok.status is S.MEETS
    short = assess(lb=limits(available_balance=D(50_602), fixed_fee=D(10)))
    assert short.status is S.FAILS


# ---------------------------------------------------------------------------
# Lágmarksveðmál
# ---------------------------------------------------------------------------

def test_below_min_stake():
    f = assess(total=1_000, la=limits(min_stake=D(600)))
    assert f.status is S.FAILS
    assert any("lágmarksveðmáli" in v for v in f.violations)


def test_min_and_max_impossible_together():
    # A má mest vera 1.000 kr., B minnst 5.000 kr. -> engin skipting gengur
    f = assess(la=limits(max_stake=D(1_000)), lb=limits(min_stake=D(5_000)))
    assert f.status is S.FAILS
    assert f.max_feasible_total is None


# ---------------------------------------------------------------------------
# Námundun og veðmálaskref
# ---------------------------------------------------------------------------

def test_stake_step_respected():
    f = assess(la=limits(stake_step=D(100)), lb=limits(stake_step=D(50)))
    assert f.side_a.stake % 100 == 0
    assert f.side_b.stake % 50 == 0
    assert f.total_cost_isk == D(100_000)


def test_coarse_step_never_exceeds_total():
    f = assess(total=100_005, la=limits(stake_step=D(10)), lb=limits(stake_step=D(10)))
    assert f.total_cost_isk <= D(100_005)
    assert f.side_a.stake % 10 == 0 and f.side_b.stake % 10 == 0


def test_fine_side_fills_remainder():
    # Epicbet-dæmið úr DEMO: B með 10 kr. skref, A fyllir upp í 100.000 kr.
    f = assess("1.47", "3.80", lb=limits(stake_step=D(10)))
    assert f.total_cost_isk == D(100_000)
    assert f.side_b.stake % 10 == 0


def test_rounding_can_erase_profit():
    f = assess("2.01", "2.00", total=100, la=limits(min_stake=D(1)), lb=limits(min_stake=D(1)))
    assert f.is_arbitrage
    assert f.worst_case_profit <= 0
    assert f.status is S.FAILS


# ---------------------------------------------------------------------------
# Gjaldmiðlar
# ---------------------------------------------------------------------------

EUR = dict(currency="EUR", stake_step=D("0.01"), min_stake=D("0.50"), max_stake=D(5_000), available_balance=D(5_000))


def test_eur_account():
    f = assess(la=limits(**EUR))
    assert f.side_a.currency == "EUR"
    assert f.side_a.stake == D("329.32")
    assert f.side_a.stake_isk == D("49398.00")
    assert f.total_cost_isk <= D(100_000)
    assert f.status is S.MEETS
    assert any("gengisáhætta" in n for n in f.notes)


def test_eur_limits_checked_in_eur():
    f = assess(la=limits(**{**EUR, "max_stake": D(300)}))  # 300 EUR = 45.000 kr. < 49.398 kr.
    assert f.status is S.FAILS
    assert any("EUR" in v for v in f.violations)


def test_missing_fx_rate_is_incomparable():
    f = assess(la=limits(**EUR), fx_rates={})
    assert f.status is S.STALE_OR_INCOMPARABLE
    assert f.side_a is None and f.worst_case_profit is None
    assert any("gengi" in r for r in f.incomparable)


def test_unknown_currency_is_unconfirmed():
    f = assess(la=limits(currency=None))
    assert f.status is S.UNCONFIRMED
    assert "A: gjaldmiðill UNKNOWN" in f.unknowns


# ---------------------------------------------------------------------------
# Gjöld
# ---------------------------------------------------------------------------

def test_fixed_fees_reduce_profit_exactly():
    base = assess()
    fees = assess(la=limits(fixed_fee=D(500)), lb=limits(fixed_fee=D(300)))
    assert (fees.side_a.stake, fees.side_b.stake) == (base.side_a.stake, base.side_b.stake)
    assert fees.worst_case_profit == base.worst_case_profit - 800


def test_commission_matches_phase2():
    f = assess(la=limits(commission_pct=D(5)))
    p2 = calculate_arbitrage("2.10", "2.05", 100_000, BetLimits(commission_pct=5), BetLimits())
    assert (f.side_a.stake, f.side_b.stake) == (p2.stake_a, p2.stake_b)
    assert f.worst_case_profit == p2.worst_case_profit


def test_fees_can_kill_profit():
    f = assess(la=limits(commission_pct=D(10)), lb=limits(commission_pct=D(10)))
    assert f.is_arbitrage
    assert f.worst_case_profit < 0
    assert f.status is S.FAILS
    assert f.max_feasible_total is None


# ---------------------------------------------------------------------------
# Hæsta framkvæmanlega fjárhæð
# ---------------------------------------------------------------------------

def test_max_feasible_total_with_low_max_stake():
    la = limits(max_stake=D(40_000))
    f = assess(la=la)
    # 40.000 kr. á A (× 2,10 = 84.000) og 40.977 kr. á B (× 2,05 = 84.002,85)
    assert f.max_feasible_total == D(80_977)
    assert f.max_feasible_worst_profit == D(84_000) - D(80_977)
    # Nákvæmlega hæsta: sú fjárhæð gengur, einni krónu meira ekki
    at_max = assess(la=la, total=80_977)
    assert at_max.status is S.MEETS
    assert at_max.side_a.stake == D(40_000)
    assert assess(la=la, total=80_978).status is S.FAILS


def _assert_is_true_maximum(la, lb, expected_max):
    """Hæsta fjárhæð uppfyllir skilyrði, einni krónu meira ekki, og bæði mörk virt."""
    f = assess(la=la, lb=lb)
    assert f.max_feasible_total == expected_max
    at_max = assess(la=la, lb=lb, total=expected_max)
    assert at_max.status is S.MEETS
    assert at_max.worst_case_profit > 0
    assert at_max.worst_case_profit == f.max_feasible_worst_profit
    for side, lim in ((at_max.side_a, la), (at_max.side_b, lb)):
        assert side.stake <= lim.max_stake
        assert side.stake + (lim.fixed_fee or 0) <= lim.available_balance
    assert assess(la=la, lb=lb, total=expected_max + 1).status is S.FAILS
    return at_max


def test_100k_rejected_when_one_side_max_is_30k():
    la = limits(max_stake=D(30_000))
    f = assess(la=la)  # 100.000 kr.
    # Nauðsynlegt veðmál á A er 49.398 kr. > 30.000 kr. hámark
    assert f.side_a.stake == D(49_398) > D(30_000)
    assert f.status is S.FAILS
    assert not f.meets_conditions
    assert f.violations == ("A: 49.398 kr. er yfir hámarksveðmáli 30.000 kr.",)
    # 30.000 × 2,10 = 63.000 ; 30.733 × 2,05 = 63.002,65 -> hæst 60.733 kr.
    at_max = _assert_is_true_maximum(la, limits(), D(60_733))
    assert (at_max.side_a.stake, at_max.side_b.stake) == (D(30_000), D(30_733))
    assert at_max.worst_case_profit == D(63_000) - D(60_733)


def test_max_feasible_respects_both_sides_limits():
    # A hámark 30.000 kr. og B hámark 25.000 kr.: B takmarkar meira
    la, lb = limits(max_stake=D(30_000)), limits(max_stake=D(25_000))
    f = assess(la=la, lb=lb)
    assert f.status is S.FAILS
    assert len(f.violations) == 2  # bæði A og B yfir hámarki
    at_max = _assert_is_true_maximum(la, lb, D(49_406))
    assert (at_max.side_a.stake, at_max.side_b.stake) == (D(24_406), D(25_000))
    # Lægra en ef aðeins A væri takmarkað
    assert D(49_406) < D(60_733)


def test_max_feasible_respects_other_sides_balance_and_fee():
    la = limits(max_stake=D(30_000))
    lb = limits(available_balance=D(28_000), fixed_fee=D(100))
    f = assess(la=la, lb=lb)
    assert f.status is S.FAILS
    at_max = _assert_is_true_maximum(la, lb, f.max_feasible_total)
    assert at_max.side_b.stake + 100 <= 28_000
    assert f.max_feasible_total < D(60_733)


def test_max_feasible_total_with_low_balance():
    lb = limits(available_balance=D(30_000), fixed_fee=D(20))
    f = assess(lb=lb)
    t = f.max_feasible_total
    assert t is not None and t < 100_000
    at_max = assess(lb=lb, total=t)
    assert at_max.status is S.MEETS
    assert at_max.side_b.stake + 20 <= 30_000
    assert assess(lb=lb, total=t + 1).status is S.FAILS


def test_max_feasible_total_eur():
    la = limits(**{**EUR, "max_stake": D(200)})  # 200 EUR = 30.000 kr.
    f = assess(la=la)
    assert f.status is S.FAILS
    t = f.max_feasible_total
    assert t is not None
    at_max = assess(la=la, total=t)
    assert at_max.status is S.MEETS
    assert at_max.side_a.stake <= 200


def test_max_feasible_reported_when_requested_fits():
    f = assess(la=limits(max_stake=D(300_000), available_balance=D(200_000)))
    assert f.status is S.MEETS
    assert f.max_feasible_total > 100_000


def test_max_feasible_requires_positive_profit():
    # Föst gjöld 500 kr. en hámark 5.000 kr. -> hagnaður getur aldrei orðið jákvæður
    f = assess(la=limits(fixed_fee=D(500), max_stake=D(5_000)))
    assert f.max_feasible_total is None


def test_no_max_feasible_without_arbitrage():
    f = assess("1.90", "1.90", la=limits(max_stake=D(10_000)))
    assert not f.is_arbitrage
    assert f.status is S.FAILS
    assert f.max_feasible_total is None


# ---------------------------------------------------------------------------
# Staðfestingarstaða og uppruni
# ---------------------------------------------------------------------------

def test_stale_limits():
    f = assess(la=limits(last_confirmed_at=NOW - timedelta(hours=30)))
    assert f.status is S.STALE_OR_INCOMPARABLE
    assert any("úrelt" in r for r in f.incomparable)


def test_custom_limits_age():
    la = limits(last_confirmed_at=NOW - timedelta(hours=2))
    assert assess(la=la).status is S.MEETS
    assert assess(la=la, max_limits_age=timedelta(hours=1)).status is S.STALE_OR_INCOMPARABLE


def test_status_priority():
    # Úrelt > uppfyllir ekki > óstaðfest > uppfyllir
    stale_and_failing = assess(la=limits(max_stake=D(1_000), last_confirmed_at=NOW - timedelta(days=3)))
    assert stale_and_failing.status is S.STALE_OR_INCOMPARABLE
    failing_and_unknown = assess(la=limits(max_stake=D(1_000)), lb=limits(available_balance=None))
    assert failing_and_unknown.status is S.FAILS


def test_extra_assumptions():
    assert assess(extra_unconfirmed=("fuzzy",)).status is S.UNCONFIRMED
    assert assess(extra_incomparable=("ekki samtímis",)).status is S.STALE_OR_INCOMPARABLE


def test_status_labels_do_not_promise_acceptance():
    for s in S:
        assert "framkvæmanlegt" not in s.value.lower()
        assert "samþykkt" not in s.value.lower()
    assert "ekki trygging" in DISCLAIMER


@pytest.mark.parametrize(
    "src_a,src_b,odds,expected",
    [
        (DataOrigin.API, DataOrigin.API, DataOrigin.API, DataOrigin.API),
        (DataOrigin.MANUAL, DataOrigin.API, DataOrigin.API, DataOrigin.MANUAL),
        (DataOrigin.API, DataOrigin.API, DataOrigin.DEMO, DataOrigin.DEMO),
        (DataOrigin.DEMO, DataOrigin.MANUAL, DataOrigin.API, DataOrigin.DEMO),
    ],
)
def test_data_basis_is_weakest_source(src_a, src_b, odds, expected):
    f = assess(la=limits(source=src_a), lb=limits(source=src_b), odds_origin=odds)
    assert f.data_basis is expected


def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        limits(min_stake=D(1000), max_stake=D(500))
    with pytest.raises(ValueError):
        limits(stake_step=D(0))
    with pytest.raises(ValueError):
        limits(currency="EURO")
    with pytest.raises(ValueError):
        limits(max_stake=D(-1))
