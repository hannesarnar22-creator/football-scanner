"""Mælaborð: síur, röðun, yfirlitstölur, áhættustöður og aldur stuðla (áfangi 6)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.dashboard import (
    HIGH_MARGIN_PCT,
    DashboardFilters,
    apply_filters,
    build_rows,
    compute_kpis,
    high_margin_rows,
    sort_rows,
)
from scanner.demo import run_demo_scan
from scanner.demo_data import default_anchor
from scanner.models import Bookmaker, Outcome, StakeLimits, DataOrigin, btts, over_under
from scanner.risk import RISK_DISCLAIMER, RiskStatus, assess_risk, format_age, odds_age
from scanner.scan import ScanReport
from tests.helpers import BOOKS, KICKOFF, NOW, _known, event, run

D = Decimal
O, U, Y, N = Outcome.OVER, Outcome.UNDER, Outcome.YES, Outcome.NO
OU25, OU15 = over_under("2.5"), over_under("1.5")
ALL = DashboardFilters()


@pytest.fixture(scope="module")
def demo_report():
    return run_demo_scan(NOW)


@pytest.fixture(scope="module")
def demo_rows(demo_report):
    return build_rows(demo_report, NOW)


def pair(home, away, market, oa, ob, bm_a="coolbet", bm_b="bet365", age_a=30, age_b=30, **kw):
    out_a, out_b = (Y, N) if market == btts() else (O, U)
    return [event(bm_a, home, away, (market, out_a, oa, age_a), **kw),
            event(bm_b, home, away, (market, out_b, ob, age_b), **kw)]


def rows_for(*pairs, books=None, **kw):
    report = run(*[e for p in pairs for e in p], books=books, **kw)
    return report, build_rows(report, NOW)


# ---------------------------------------------------------------------------
# 5% síun
# ---------------------------------------------------------------------------

def test_high_margin_rows_at_least_5_percent(demo_rows):
    high = high_margin_rows(demo_rows, ALL, NOW)
    assert high and all(r.margin_pct >= HIGH_MARGIN_PCT for r in high)
    assert {r.opportunity.match.label for r in high} == {
        "Eintracht Frankfurt – VfB Stuttgart", "Bayern München – Borussia Dortmund"}
    # Ekkert ≥ 5% vantar
    assert len(high) == sum(r.margin_pct >= 5 for r in demo_rows)


def test_exactly_5_percent_included():
    # 2,10 / 2,10 -> S = 0,952380... -> framlegð nákvæmlega 5%
    _, rows = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.10"))
    assert rows[0].margin_pct == D(5)
    assert len(high_margin_rows(rows, ALL, NOW)) == 1


def test_high_margin_respects_other_filters(demo_rows):
    f = DashboardFilters(competitions=frozenset({"Bundesliga"}), markets=frozenset({"Over/Under 1.5"}))
    high = high_margin_rows(demo_rows, f, NOW)
    assert [r.opportunity.match.label for r in high] == ["Bayern München – Borussia Dortmund"]


def test_high_margin_keeps_higher_min(demo_rows):
    high = high_margin_rows(demo_rows, DashboardFilters(min_margin_pct=D("6.2")), NOW)
    assert [round(r.margin_pct, 2) for r in high] == [D("6.46")]


# ---------------------------------------------------------------------------
# Röðun
# ---------------------------------------------------------------------------

def test_sorted_by_margin_desc(demo_rows):
    margins = [r.margin_pct for r in demo_rows]
    assert margins == sorted(margins, reverse=True)
    filtered = apply_filters(demo_rows, DashboardFilters(min_margin_pct=D(2)), NOW)
    assert [r.margin_pct for r in filtered] == sorted((r.margin_pct for r in filtered), reverse=True)


def test_sort_tiebreak_by_profit_then_key():
    _, rows = rows_for(
        pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"),
        pair("Liverpool", "Chelsea", OU25, "2.05", "2.10"),
    )
    assert rows[0].margin_pct == rows[1].margin_pct
    assert sort_rows(list(reversed(rows))) == rows  # stöðug röð óháð upprunalegri röð


def test_only_arbitrage_rows(demo_report, demo_rows):
    assert len(demo_rows) == len(demo_report.arbitrage)
    assert all(r.opportunity.feasibility.is_arbitrage for r in demo_rows)


# ---------------------------------------------------------------------------
# Samræmi yfirlitstalna og töflu
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "f",
    [
        ALL,
        DashboardFilters(min_margin_pct=D(3)),
        DashboardFilters(min_margin_pct=D(5)),
        DashboardFilters(competitions=frozenset({"Bundesliga", "La Liga"})),
        DashboardFilters(markets=frozenset({"BTTS (Yes/No)"})),
        DashboardFilters(bookmakers=frozenset({"Coolbet", "Epicbet"})),
        DashboardFilters(statuses=frozenset({RiskStatus.MEETS})),
        DashboardFilters(min_margin_pct=D(50)),
    ],
)
def test_kpis_match_table(demo_report, demo_rows, f):
    table = apply_filters(demo_rows, f, NOW)
    k = compute_kpis(demo_report, table, f, NOW, NOW)
    assert k.arbitrage_count == len(table)
    assert k.high_margin_count == len(high_margin_rows(demo_rows, f, NOW)) == sum(r.margin_pct >= 5 for r in table)
    assert k.best_margin_pct == (table[0].margin_pct if table else None)
    profits = [r.worst_case_profit for r in table if r.worst_case_profit is not None]
    assert k.best_profit == (max(profits) if profits else None)


def test_kpi_match_count(demo_report, demo_rows):
    assert compute_kpis(demo_report, demo_rows, ALL, NOW, NOW).match_count == 59
    bl = DashboardFilters(competitions=frozenset({"Bundesliga"}))
    expected = sum(m.competition_name == "Bundesliga" for m in demo_report.matches)
    assert compute_kpis(demo_report, apply_filters(demo_rows, bl, NOW), bl, NOW, NOW).match_count == expected


def test_kpis_not_hardcoded():
    report, rows = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"))
    k = compute_kpis(report, rows, ALL, NOW, NOW)
    assert (k.match_count, k.arbitrage_count, k.high_margin_count) == (1, 1, 0)
    assert round(k.best_margin_pct, 4) == D("3.7349")
    assert k.best_profit == D("3734.10")


# ---------------------------------------------------------------------------
# Hagnaður
# ---------------------------------------------------------------------------

def test_profit_values_correct(demo_rows):
    for r in demo_rows:
        f = r.opportunity.feasibility
        a, b = f.side_a, f.side_b
        assert f.profit_if_a == a.payout_isk - f.total_cost_isk - a.fee_isk - b.fee_isk
        assert f.profit_if_b == b.payout_isk - f.total_cost_isk - a.fee_isk - b.fee_isk
        assert f.worst_case_profit == min(f.profit_if_a, f.profit_if_b)
        assert a.stake_isk + b.stake_isk == f.total_cost_isk <= f.requested_total


def test_best_profit_row_example():
    _, rows = rows_for(pair("Bayern", "Dortmund", OU15, "1.47", "3.80", competition="Bundesliga"))
    f = rows[0].opportunity.feasibility
    assert (f.side_a.stake, f.side_b.stake) == (D(72_106), D(27_894))
    assert f.profit_if_a == D(72_106) * D("1.47") - D(100_000)
    assert f.profit_if_b == D(27_894) * D("3.80") - D(100_000)


# ---------------------------------------------------------------------------
# Aldur stuðla
# ---------------------------------------------------------------------------

def test_odds_age_uses_oldest_quote():
    report, rows = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", age_a=10, age_b=95))
    assert rows[0].risk.odds_age == timedelta(seconds=95)
    assert odds_age(report.arbitrage[0], NOW + timedelta(seconds=5)) == timedelta(seconds=100)


@pytest.mark.parametrize("secs,text", [(5, "5 sek."), (95, "1 mín. 35 sek."), (3725, "1 klst. 2 mín.")])
def test_format_age(secs, text):
    assert format_age(timedelta(seconds=secs)) == text


def test_max_odds_age_filter():
    _, rows = rows_for(
        pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", age_a=20, age_b=20),
        pair("Liverpool", "Chelsea", OU25, "2.10", "2.05", age_a=20, age_b=100),
    )
    f = DashboardFilters(max_odds_age=timedelta(minutes=1))
    assert [r.opportunity.match.label for r in apply_filters(rows, f, NOW)] == ["Arsenal – Chelsea"]


def test_old_odds_marked_stale():
    report, _ = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"))
    later = NOW + timedelta(minutes=10)  # stuðlarnir eru orðnir 10 mín. gamlir
    risk = assess_risk(report.arbitrage[0], later)
    assert risk.status is RiskStatus.STALE
    assert "gamlir" in risk.reasons[0]


# ---------------------------------------------------------------------------
# Áhættustöður
# ---------------------------------------------------------------------------

def test_risk_meets():
    _, rows = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"))
    assert rows[0].risk.status is RiskStatus.MEETS


def test_risk_unconfirmed_when_unknown_max():
    books = {**BOOKS, "bet365": _known("bet365", "bet365", max_stake=None)}
    _, rows = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"), books=books)
    assert rows[0].risk.status is RiskStatus.UNCONFIRMED
    assert any("hámarksveðmál UNKNOWN" in r for r in rows[0].risk.reasons)


def test_risk_fails_when_max_too_low():
    books = {**BOOKS, "coolbet": _known("coolbet", "Coolbet", max_stake=D(30_000))}
    _, rows = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"), books=books)
    assert rows[0].risk.status is RiskStatus.FAILS
    assert any("hámarksveðmáli" in r for r in rows[0].risk.reasons)


def test_risk_stale_when_not_simultaneous():
    _, rows = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", age_a=10, age_b=250))
    assert rows[0].risk.status is RiskStatus.STALE
    assert any("ekki samtímis" in r for r in rows[0].risk.reasons)


def test_risk_stale_when_limits_old():
    books = {**BOOKS, "coolbet": _known("coolbet", "Coolbet", last_confirmed_at=NOW - timedelta(days=2))}
    _, rows = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"), books=books)
    assert rows[0].risk.status is RiskStatus.STALE


def test_risk_incomparable_without_fx_rate():
    books = {**BOOKS, "coolbet": _known("coolbet", "Coolbet", currency="EUR", stake_step=D("0.01"))}
    _, rows = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"), books=books)
    assert rows[0].risk.status is RiskStatus.INCOMPARABLE


def test_risk_incomparable_when_markets_differ():
    report, _ = rows_for(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"))
    o = report.arbitrage[0]
    tampered = replace(o, quote_b=o.quote_b.model_copy(update={"market": over_under("3.5")}))
    risk = assess_risk(tampered, NOW)
    assert risk.status is RiskStatus.INCOMPARABLE
    assert "Markaðir ekki eins" in risk.reasons[0]


def test_five_statuses_and_wording():
    assert [s.value for s in RiskStatus] == [
        "Uppfyllir skráð skilyrði", "Óstaðfest", "Úrelt", "Uppfyllir ekki skilyrði", "Ósambærilegir markaðir"]
    assert RiskStatus.MEETS.color == "green"
    assert RiskStatus.UNCONFIRMED.color == "yellow"
    assert {RiskStatus.STALE.color, RiskStatus.FAILS.color, RiskStatus.INCOMPARABLE.color} == {"red"}
    for s in RiskStatus:
        assert "örugg" not in s.value.lower() and "áhættulaus" not in s.value.lower()
    assert "Ekkert tækifæri er öruggt eða áhættulaust" in RISK_DISCLAIMER


def test_status_filter(demo_rows):
    f = DashboardFilters(statuses=frozenset({RiskStatus.MEETS}))
    rows = apply_filters(demo_rows, f, NOW)
    assert rows and all(r.risk.status is RiskStatus.MEETS for r in rows)
    assert len(rows) < len(demo_rows)


def test_demo_risk_variety(demo_rows):
    assert {RiskStatus.MEETS, RiskStatus.UNCONFIRMED, RiskStatus.STALE, RiskStatus.FAILS} <= {
        r.risk.status for r in demo_rows}


# ---------------------------------------------------------------------------
# Aðrar síur
# ---------------------------------------------------------------------------

def test_bookmaker_filter_requires_both_sides(demo_rows):
    f = DashboardFilters(bookmakers=frozenset({"Coolbet", "Epicbet"}))
    rows = apply_filters(demo_rows, f, NOW)
    assert rows and all({r.opportunity.bookmaker_a.name, r.opportunity.bookmaker_b.name} <= f.bookmakers
                        for r in rows)
    assert not any("bet365" in (r.opportunity.bookmaker_a.id, r.opportunity.bookmaker_b.id) for r in rows)


def test_kickoff_filter():
    report, rows = rows_for(
        pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", kickoff=NOW + timedelta(hours=3)),
        pair("Liverpool", "Chelsea", OU25, "2.10", "2.05", kickoff=NOW + timedelta(hours=30)),
    )
    f = DashboardFilters(kickoff_within=timedelta(hours=6))
    assert [r.opportunity.match.label for r in apply_filters(rows, f, NOW)] == ["Arsenal – Chelsea"]
    assert compute_kpis(report, apply_filters(rows, f, NOW), f, NOW, NOW).match_count == 1


# ---------------------------------------------------------------------------
# Tómar niðurstöður
# ---------------------------------------------------------------------------

def test_empty_report():
    report = ScanReport()
    rows = build_rows(report, NOW)
    k = compute_kpis(report, rows, ALL, NOW, None)
    assert rows == [] and high_margin_rows(rows, ALL, NOW) == []
    assert (k.match_count, k.arbitrage_count, k.high_margin_count) == (0, 0, 0)
    assert k.best_margin_pct is None and k.best_profit is None and k.last_update is None


def test_filters_hiding_everything(demo_report, demo_rows):
    f = DashboardFilters(min_margin_pct=D(50))
    table = apply_filters(demo_rows, f, NOW)
    k = compute_kpis(demo_report, table, f, NOW, NOW)
    assert table == [] and k.arbitrage_count == 0 and k.best_margin_pct is None
    assert k.match_count == 59  # leikir eru enn til – aðeins framlegðarsía felur tækifærin


# ---------------------------------------------------------------------------
# DEMO-uppfærsla: engin ný tilbúin tækifæri, engar stuðlabreytingar
# ---------------------------------------------------------------------------

def test_refresh_keeps_demo_dataset():
    anchor = default_anchor(NOW)
    first = run_demo_scan(NOW, anchor=anchor)
    for later in (NOW + timedelta(minutes=7), NOW + timedelta(hours=13)):  # líka yfir miðnætti
        again = run_demo_scan(later, anchor=anchor)
        assert [o.key for o in again.arbitrage] == [o.key for o in first.arbitrage]
        assert [(o.quote_a.price, o.quote_b.price) for o in again.arbitrage] == \
               [(o.quote_a.price, o.quote_b.price) for o in first.arbitrage]
