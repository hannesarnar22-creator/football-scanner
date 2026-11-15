from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.demo import run_demo_scan, summarize
from scanner.demo_data import build_demo_events
from scanner.feasibility import FeasibilityStatus
from scanner.models import DataOrigin

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def events():
    return build_demo_events(NOW)


@pytest.fixture(scope="module")
def report():
    return run_demo_scan(NOW)


def test_at_least_50_matches(report):
    assert report.match_count >= 50


def test_everything_marked_demo(events, report):
    assert all(e.source.is_demo for e in events)
    assert all(q.source.is_demo for e in events for q in e.quotes)
    assert all(o.is_demo for o in report.opportunities)


@pytest.mark.parametrize("threshold", [2, 3, 5])
def test_opportunities_above_thresholds(report, threshold):
    assert any(o.margin_pct > threshold for o in report.arbitrage)


def test_contains_no_arbitrage_cases(report):
    assert sum(not o.feasibility.is_arbitrage for o in report.opportunities) > 100


def test_contains_varied_team_names(events):
    names = {e.home_team for e in events if e.home_team.lower().startswith("man") and "city" not in e.home_team.lower()}
    assert {"Manchester United", "Man Utd", "Manchester U."} <= names


def test_contains_stale_and_wrong_lines(report):
    reasons = [x.reason for x in report.excluded]
    assert any("Úreltur" in r for r in reasons)
    assert any("Asian" in r for r in reasons)
    assert any("Fyrri hálfleikur" in r for r in reasons)
    assert any("Lína ekki studd" in r for r in reasons)
    assert any("Tvítekinn" in r for r in reasons)
    assert any("pre-match" in r for r in reasons)


def test_contains_uncertain_matches(report):
    assert sum("Óvíst" in x.reason for x in report.review) >= 2
    assert any("Upphafstími" in x.reason for x in report.review)


def test_stale_quote_does_not_create_arbitrage(report):
    inter = [o for o in report.opportunities if o.match.label == "Inter – Juventus" and o.market.label == "Over/Under 3.5"]
    assert inter and not inter[0].feasibility.is_arbitrage
    assert inter[0].quote_a.price != Decimal("6.50")


def test_status_variety(report):
    statuses = {o.status for o in report.arbitrage}
    assert set(FeasibilityStatus) == statuses


def test_demo_limits_marked_demo(report):
    assert all(o.data_basis is DataOrigin.DEMO for o in report.opportunities)
    assert all(o.bookmaker_a.limits.source is DataOrigin.DEMO for o in report.opportunities)


def test_bet365_unknown_max_never_meets(report):
    for o in report.opportunities:
        if "bet365" in (o.bookmaker_a.id, o.bookmaker_b.id):
            assert o.status is not FeasibilityStatus.MEETS


def test_fuzzy_assumption_present(report):
    assert any("fuzzy" in a for o in report.arbitrage for a in o.assumptions)


def test_deterministic():
    a = build_demo_events(NOW)
    b = build_demo_events(NOW)
    assert a == b


def test_all_stakes_within_total(report):
    for o in report.opportunities:
        f = o.feasibility
        assert f.side_a.stake_isk + f.side_b.stake_isk == f.total_cost_isk
        # Aldrei yfir heildarfjárhæð; í mesta lagi stærsta skref (10 kr.) undir
        assert Decimal(99_990) <= f.total_cost_isk <= Decimal(100_000)


def test_summary(report):
    s = summarize(report, NOW)
    assert s.match_count == report.match_count
    assert s.arbitrage_count == len(report.arbitrage) > 0
    assert 0 < s.meets_count < s.arbitrage_count
    assert s.best_margin_pct == max(o.margin_pct for o in report.arbitrage)
    assert s.last_updated <= NOW
