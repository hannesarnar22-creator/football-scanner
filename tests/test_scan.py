"""Samsvörun leikja og arbitrage-leit yfir samræmd gögn."""

from datetime import timedelta
from decimal import Decimal

from scanner.arbitrage import calculate_arbitrage
from scanner.feasibility import FeasibilityStatus
from scanner.models import Outcome, Period, TotalType, btts, over_under
from tests.helpers import BOOKS, KICKOFF, NOW, _known, event, run  # noqa: F401

D = Decimal
O, U, Y, N = Outcome.OVER, Outcome.UNDER, Outcome.YES, Outcome.NO
OU15, OU25, OU35, BTTS = over_under("1.5"), over_under("2.5"), over_under("3.5"), btts()


# ---------------------------------------------------------------------------
# Rétt samsvörun leikja
# ---------------------------------------------------------------------------

def test_alias_names_match_and_phase2_math_holds():
    r = run(
        event("coolbet", "Manchester United", "Liverpool", (OU25, O, "2.10"), home_code="MUN", away_code="LIV"),
        event("bet365", "Man Utd", "Liverpool FC", (OU25, U, "2.05"), competition="EPL"),
        event("epicbet", "Manchester U.", "Liverpool", (OU25, O, "1.90"), competition="England - Premier League"),
    )
    assert r.match_count == 1
    assert len(r.opportunities) == 1
    o = r.opportunities[0]
    assert o.match.label == "Manchester United – Liverpool"
    assert (o.quote_a.bookmaker_id, o.quote_b.bookmaker_id) == ("coolbet", "bet365")
    # Sömu tölur og í áfanga 2
    expected = calculate_arbitrage("2.10", "2.05", 100_000)
    f = o.feasibility
    assert (f.side_a.stake, f.side_b.stake) == (expected.stake_a, expected.stake_b) == (D(49398), D(50602))
    assert f.worst_case_profit == D("3734.10")
    assert f.requested_total == f.total_cost_isk == D(100_000)
    assert o.status is FeasibilityStatus.MEETS
    assert o.assumptions == ()


def test_total_stake_is_passed_through():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10")),
        event("bet365", "Arsenal FC", "Chelsea FC", (OU25, U, "2.05")),
        total_stake=200_000,
    )
    f = r.opportunities[0].feasibility
    expected = calculate_arbitrage("2.10", "2.05", 200_000)
    assert f.side_a.stake + f.side_b.stake == D(200_000)
    assert (f.side_a.stake, f.side_b.stake) == (expected.stake_a, expected.stake_b)
    assert (f.profit_if_a, f.profit_if_b) == (expected.profit_if_a, expected.profit_if_b)


def test_icelandic_names_match():
    r = run(
        event("coolbet", "Breiðablik", "Víkingur Reykjavík", (BTTS, Y, "2.20"), competition="Besta deild karla"),
        event("bet365", "Breidablik", "Vikingur R", (BTTS, N, "1.95"), competition="Iceland - Besta deild"),
    )
    assert len(r.opportunities) == 1


# ---------------------------------------------------------------------------
# Ólíkir leikir blandast ekki saman
# ---------------------------------------------------------------------------

def test_different_matches_not_mixed():
    r = run(
        event("coolbet", "Manchester United", "Liverpool", (OU25, O, "2.60")),
        event("bet365", "Manchester United", "Arsenal", (OU25, U, "2.60")),
    )
    assert r.match_count == 2
    assert r.opportunities == []


def test_swapped_home_away_not_mixed():
    r = run(
        event("coolbet", "Manchester United", "Liverpool", (OU25, O, "2.60")),
        event("bet365", "Liverpool", "Manchester United", (OU25, U, "2.60")),
    )
    assert r.opportunities == []


def test_same_names_in_different_competitions_not_mixed():
    r = run(
        event("coolbet", "Manchester United", "Liverpool", (OU25, O, "2.60")),
        event("bet365", "Manchester United", "Liverpool", (OU25, U, "2.60"), competition="La Liga"),
    )
    assert r.opportunities == []


def test_different_kickoff_not_mixed_and_flagged():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.60")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.60"), kickoff=KICKOFF + timedelta(hours=1)),
    )
    assert r.opportunities == []
    assert len(r.review) == 2
    assert all("Upphafstími" in x.reason for x in r.review)
    assert r.match_count == 1


def test_kickoff_conflict_marks_remaining_pair_unconfirmed():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05")),
        event("epicbet", "Arsenal", "Chelsea", (OU25, U, "2.50"), kickoff=KICKOFF + timedelta(hours=2)),
    )
    assert len(r.opportunities) == 1
    o = r.opportunities[0]
    assert o.quote_b.price == D("2.05")  # stuðull epicbet ekki notaður
    assert o.status is FeasibilityStatus.UNCONFIRMED
    assert any("upphafstími" in a for a in o.assumptions)


def test_uncertain_team_name_not_used():
    r = run(
        event("coolbet", "Manchester United", "Liverpool", (OU25, O, "2.10")),
        event("bet365", "Man", "Liverpool", (OU25, U, "2.50")),
    )
    assert r.opportunities == []
    assert len(r.review) == 1
    assert "Óvíst" in r.review[0].reason


def test_unknown_competition_goes_to_review():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05"), competition="Premier Leag"),
    )
    assert r.opportunities == []
    assert "Óþekkt keppni" in r.review[0].reason


def test_confident_fuzzy_used_but_unconfirmed():
    r = run(
        event("coolbet", "Manchester United", "Liverpool", (OU25, O, "2.10")),
        event("bet365", "Manchestr United", "Liverpool", (OU25, U, "2.05")),
    )
    o = r.opportunities[0]
    assert o.status is FeasibilityStatus.UNCONFIRMED
    assert any("fuzzy" in a for a in o.assumptions)


# ---------------------------------------------------------------------------
# Ólíkar markaðslínur og reglur blandast ekki saman
# ---------------------------------------------------------------------------

def test_under25_and_over35_not_mixed():
    # Ef þessu væri blandað saman: S = 1/2.60 + 1/2.50 = 0,785 -> fölsk 27% "arbitrage"
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, U, "2.60")),
        event("bet365", "Arsenal", "Chelsea", (OU35, O, "2.50")),
    )
    assert r.opportunities == []


def test_over25_and_under35_not_mixed():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.40")),
        event("bet365", "Arsenal", "Chelsea", (OU35, U, "2.40")),
    )
    assert r.opportunities == []


def test_first_half_not_mixed_with_full_match():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (over_under("1.5", period=Period.FIRST_HALF), O, "3.40")),
        event("bet365", "Arsenal", "Chelsea", (OU15, U, "3.50")),
    )
    assert r.opportunities == []
    assert any("Fyrri hálfleikur" in x.reason for x in r.excluded)


def test_asian_total_not_mixed():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (over_under("2.5", total_type=TotalType.ASIAN), O, "2.40")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.40")),
    )
    assert r.opportunities == []
    assert any("Asian" in x.reason for x in r.excluded)


def test_extra_time_market_excluded():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (over_under("2.5", period=Period.INCLUDING_EXTRA_TIME), O, "2.40")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.40")),
    )
    assert r.opportunities == []


def test_unsupported_line_excluded():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (over_under("4.5"), O, "6.00")),
        event("bet365", "Arsenal", "Chelsea", (over_under("4.5"), U, "1.30")),
    )
    assert r.opportunities == []
    assert sum("Lína ekki studd" in x.reason for x in r.excluded) == 2


def test_line_written_differently_is_same_market():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (over_under("2.50"), O, "2.10")),
        event("bet365", "Arsenal", "Chelsea", (over_under("2.5"), U, "2.05")),
    )
    assert len(r.opportunities) == 1


def test_btts_not_mixed_with_over_under():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (BTTS, Y, "2.40")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.40")),
    )
    assert r.opportunities == []


# ---------------------------------------------------------------------------
# BTTS
# ---------------------------------------------------------------------------

def test_btts_yes_no():
    r = run(
        event("epicbet", "Real Madrid", "Atlético Madrid", (BTTS, Y, "2.20"), (BTTS, N, "1.60"), competition="La Liga"),
        event("coolbet", "Real Madrid", "Atletico Madrid", (BTTS, Y, "1.85"), (BTTS, N, "1.95"), competition="LaLiga"),
    )
    assert len(r.opportunities) == 1
    o = r.opportunities[0]
    assert (o.quote_a.outcome, o.quote_b.outcome) == (Y, N)
    assert (o.quote_a.price, o.quote_b.price) == (D("2.20"), D("1.95"))
    assert round(o.margin_pct, 2) == D("3.37")
    assert o.feasibility.is_arbitrage


def test_btts_no_arbitrage():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (BTTS, Y, "1.80"), (BTTS, N, "1.95")),
        event("bet365", "Arsenal", "Chelsea", (BTTS, Y, "1.85"), (BTTS, N, "1.90")),
    )
    o = r.opportunities[0]
    assert not o.feasibility.is_arbitrage
    assert o.status is FeasibilityStatus.FAILS
    assert r.arbitrage == []


# ---------------------------------------------------------------------------
# Úreltir stuðlar og tímastimplar
# ---------------------------------------------------------------------------

def test_stale_quote_excluded():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.60", 600)),  # 10 mín.
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05")),
    )
    assert r.opportunities == []
    assert any("Úreltur" in x.reason for x in r.excluded)


def test_stale_quote_replaced_by_fresh_alternative():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.60", 600)),
        event("epicbet", "Arsenal", "Chelsea", (OU25, O, "1.90")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05")),
    )
    o = r.opportunities[0]
    assert o.quote_a.bookmaker_id == "epicbet"
    assert not o.feasibility.is_arbitrage


def test_bookmaker_updated_at_used_for_staleness():
    from tests.helpers import quote

    ev = event("coolbet", "Arsenal", "Chelsea")
    ev = ev.model_copy(update={"quotes": (quote("coolbet", OU25, O, "2.60", age=10, updated_age=900),)})
    r = run(ev, event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05")))
    assert r.opportunities == []


def test_custom_max_age():
    evs = (
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10", 200)),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05", 200)),
    )
    assert len(run(*evs).opportunities) == 1
    assert run(*evs, max_age=timedelta(seconds=60)).opportunities == []


def test_future_timestamp_excluded():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10", -600)),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05")),
    )
    assert r.opportunities == []
    assert any("framtíðinni" in x.reason for x in r.excluded)


def test_started_match_excluded():
    started = NOW - timedelta(minutes=10)
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10"), kickoff=started),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05"), kickoff=started),
    )
    assert r.opportunities == []
    assert r.match_count == 0
    assert all("pre-match" in x.reason for x in r.excluded)


def test_non_simultaneous_quotes_incomparable():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10", 10)),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05", 250)),
    )
    o = r.opportunities[0]
    assert o.feasibility.worst_case_profit > 0  # fjárhæðir ganga upp...
    assert o.status is FeasibilityStatus.STALE_OR_INCOMPARABLE  # ...en tímastimplar styðja það ekki
    assert any("ekki samtímis" in a for a in o.assumptions)


def test_simultaneous_quotes_confirmed():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10", 10)),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05", 60)),
    )
    assert r.opportunities[0].status is FeasibilityStatus.MEETS


# ---------------------------------------------------------------------------
# Tvítekin gögn
# ---------------------------------------------------------------------------

def _base_events():
    return [
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10"), event_id="c1"),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05"), event_id="b1"),
    ]


def test_identical_duplicate_events_do_not_skew():
    single = run(*_base_events())
    doubled = run(*_base_events(), *_base_events())
    assert len(doubled.opportunities) == len(single.opportunities) == 1
    assert doubled.match_count == single.match_count == 1
    assert doubled.opportunities[0].feasibility == single.opportunities[0].feasibility
    assert doubled.arbitrage[0].margin_pct == single.arbitrage[0].margin_pct


def test_older_duplicate_with_higher_price_ignored():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "1.80", 20)),
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.60", 200), event_id="coolbet-dup"),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "1.98")),
    )
    o = r.opportunities[0]
    assert o.quote_a.price == D("1.80")
    assert not o.feasibility.is_arbitrage
    assert any("Tvítekinn" in x.reason for x in r.excluded)


def test_duplicate_with_same_timestamp_uses_lower_price():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.20", 30), (OU25, O, "2.10", 30)),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05")),
    )
    assert r.opportunities[0].quote_a.price == D("2.10")


def test_duplicate_does_not_count_as_second_bookmaker():
    # Sama síða tvisvar má ekki uppfylla kröfu um tvær ólíkar síður
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10")),
        event("coolbet", "Arsenal", "Chelsea", (OU25, U, "2.05"), event_id="dup"),
    )
    assert r.opportunities == []


# ---------------------------------------------------------------------------
# Val á stuðlum og veðmálasíðum
# ---------------------------------------------------------------------------

def test_best_price_selected_per_outcome():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.00"), (OU25, U, "1.80")),
        event("bet365", "Arsenal", "Chelsea", (OU25, O, "2.10"), (OU25, U, "1.85")),
        event("epicbet", "Arsenal", "Chelsea", (OU25, O, "2.05"), (OU25, U, "2.05")),
    )
    o = r.opportunities[0]
    assert (o.quote_a.bookmaker_id, o.quote_a.price) == ("bet365", D("2.10"))
    assert (o.quote_b.bookmaker_id, o.quote_b.price) == ("epicbet", D("2.05"))


def test_requires_two_different_bookmakers():
    r = run(event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.20"), (OU25, U, "2.20")))
    assert r.opportunities == []


def test_same_bookmaker_best_on_both_sides_uses_next_best():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.20"), (OU25, U, "2.00")),
        event("bet365", "Arsenal", "Chelsea", (OU25, O, "1.90"), (OU25, U, "1.85")),
        event("epicbet", "Arsenal", "Chelsea", (OU25, O, "1.88"), (OU25, U, "1.90")),
    )
    o = r.opportunities[0]
    assert o.quote_a.bookmaker_id != o.quote_b.bookmaker_id
    assert (o.quote_a.price, o.quote_b.price) == (D("2.20"), D("1.90"))


def test_one_opportunity_per_market():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU15, O, "1.40"), (OU25, O, "2.10"), (OU35, O, "3.20"), (BTTS, Y, "1.90")),
        event("bet365", "Arsenal", "Chelsea", (OU15, U, "3.00"), (OU25, U, "1.80"), (OU35, U, "1.35"), (BTTS, N, "1.90")),
    )
    assert sorted(o.market.label for o in r.opportunities) == [
        "BTTS (Yes/No)", "Over/Under 1.5", "Over/Under 2.5", "Over/Under 3.5",
    ]


def test_unknown_bookmaker_excluded():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10")),
        event("unibet", "Arsenal", "Chelsea", (OU25, U, "2.05")),
    )
    assert r.opportunities == []
    assert any("Óþekkt veðmálasíða" in x.reason for x in r.excluded)


def test_bookmaker_limits_applied():
    books = {**BOOKS, "coolbet": _known("coolbet", "Coolbet", max_stake=D(40_000))}
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05")),
        books=books,
    )
    assert r.opportunities[0].status is FeasibilityStatus.FAILS


def test_unknown_bookmaker_limits_unconfirmed():
    from scanner.models import Bookmaker

    books = {**BOOKS, "bet365": Bookmaker(id="bet365", name="bet365")}
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05")),
        books=books,
    )
    assert r.opportunities[0].status is FeasibilityStatus.UNCONFIRMED


def test_opportunities_sorted_by_margin():
    r = run(
        event("coolbet", "Arsenal", "Chelsea", (OU25, O, "2.10"), (BTTS, Y, "2.20")),
        event("bet365", "Arsenal", "Chelsea", (OU25, U, "2.05"), (BTTS, N, "1.95")),
    )
    margins = [o.margin_pct for o in r.opportunities]
    assert margins == sorted(margins, reverse=True)
