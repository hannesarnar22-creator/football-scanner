"""Gagnagrunnur, saga stuðla, auðkenni tækifæra og greining (áfangi 5)."""

import csv
import io
from datetime import UTC, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from scanner.analytics import (
    daily_new_counts,
    is_hourly,
    load_history,
    margin_series,
    summarize_history,
    time_buckets,
    to_csv,
)
from scanner.db import (
    BookmakerRow,
    CompetitionRow,
    Database,
    DataMode,
    MarketRow,
    MatchRow,
    OddsSnapshotRow,
    OpportunityObservationRow,
    OpportunityRow,
)
from scanner.history import IngestResult, ingest_scan, make_opportunity_key, opportunity_key
from scanner.models import DataSource, Outcome, Period, TotalType, btts, over_under
from tests.helpers import BOOKS, NOW, event, run

D = Decimal
O, U, Y, N = Outcome.OVER, Outcome.UNDER, Outcome.YES, Outcome.NO
OU15, OU25 = over_under("1.5"), over_under("2.5")
DEMO = DataSource(name="DEMO-próf", kind="DEMO")
LIVE = DataMode.LIVE


@pytest.fixture
def db(temp_database_url):
    database = Database(temp_database_url)
    yield database
    database.dispose()


def count(db: Database, model) -> int:
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(model))


def pair(home, away, market, oa, ob, bm_a="coolbet", bm_b="bet365", now=NOW, source=None, **kw):
    out_a, out_b = (Y, N) if market == btts() else (O, U)
    extra = {"source": source} if source else {}
    return [
        event(bm_a, home, away, (market, out_a, oa), now=now, **extra, **kw),
        event(bm_b, home, away, (market, out_b, ob), now=now, **extra),
    ]


def scan(*pairs, now=NOW):
    return run(*[e for p in pairs for e in p], now=now)


def ingest(db, report, mode=LIVE, now=NOW) -> IngestResult:
    return ingest_scan(db, report, BOOKS, mode, now)


BASE = lambda now=NOW: scan(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", now=now), now=now)  # noqa: E731


# ---------------------------------------------------------------------------
# Vistun
# ---------------------------------------------------------------------------

def test_saves_all_entities(db):
    r = ingest(db, BASE())
    assert r.new_snapshots == 2
    assert r.new_opportunities == 1
    assert count(db, CompetitionRow) == 1
    assert count(db, BookmakerRow) == 2
    assert count(db, MarketRow) == 1
    assert count(db, MatchRow) == 1
    assert count(db, OddsSnapshotRow) == 2
    assert count(db, OpportunityRow) == 1
    assert count(db, OpportunityObservationRow) == 1


def test_values_survive_reopen_exactly(db, temp_database_url):
    ingest(db, BASE())
    db.dispose()
    reopened = Database(temp_database_url)
    with reopened.session() as s:
        opp = s.scalars(select(OpportunityRow)).one()
        assert opp.mode == "LIVE"
        assert (opp.odds_a, opp.odds_b) == (D("2.10"), D("2.05"))
        assert isinstance(opp.theoretical_margin_pct, Decimal)
        assert opp.worst_case_profit == D("3734.10")
        assert opp.total_stake == D(100_000)
        assert opp.status == "Uppfyllir skráð fjárhæðarskilyrði"
        assert opp.max_stake_a == D(1_000_000) and opp.balance_b == D(1_000_000)
        assert opp.first_seen_at == NOW and opp.first_seen_at.tzinfo is UTC
        assert opp.match.label == "Arsenal – Chelsea"
        assert opp.market.label == "Over/Under 2.5"
        snap = s.scalars(select(OddsSnapshotRow).where(OddsSnapshotRow.outcome == "OVER")).one()
        assert snap.price == D("2.10")
        assert snap.bookmaker_id == "coolbet"
        assert snap.source_time == NOW - timedelta(seconds=30)  # upprunatími gagna
        assert snap.recorded_at == NOW  # skráningartími UTC
    reopened.dispose()


def test_non_arbitrage_pairs_not_stored_as_opportunities(db):
    r = ingest(db, scan(pair("Arsenal", "Chelsea", OU25, "1.90", "1.90")))
    assert r.new_opportunities == 0
    assert r.new_snapshots == 2  # stuðlarnir eru samt skráðir í sögu
    assert count(db, OpportunityRow) == 0


# ---------------------------------------------------------------------------
# Endurtekinn innlestur
# ---------------------------------------------------------------------------

def test_reingest_same_data_is_idempotent(db):
    first = ingest(db, BASE())
    before = {m: count(db, m) for m in (MatchRow, OddsSnapshotRow, OpportunityRow, OpportunityObservationRow)}
    for _ in range(3):
        again = ingest(db, BASE())
        assert again == IngestResult(0, 2, 0, 1, 0, 0)
    after = {m: count(db, m) for m in before}
    assert before == after
    assert first.new_opportunities == 1


def test_later_fetch_with_same_odds_is_not_new(db):
    ingest(db, BASE())
    later = NOW + timedelta(hours=2)
    r = ingest(db, BASE(later), now=later)
    assert r.new_opportunities == 0
    assert r.new_snapshots == 0
    assert r.new_observations == 0
    with db.session() as s:
        opp = s.scalars(select(OpportunityRow)).one()
        assert opp.first_seen_at == NOW
        assert opp.last_seen_at == later
        snaps = s.scalars(select(OddsSnapshotRow)).all()
        assert all(sn.recorded_at == NOW and sn.last_seen_at == later for sn in snaps)


def test_demo_scan_repeated_is_idempotent(db):
    from scanner.demo import run_demo_scan
    from scanner.demo_data import demo_bookmakers

    now = NOW
    first = ingest_scan(db, run_demo_scan(now), demo_bookmakers(now), DataMode.DEMO, now)
    assert first.new_opportunities == 9
    # Ný keyrsla sama dag (eins og endurræsing Streamlit) býr ekki til ný tækifæri
    later = NOW + timedelta(hours=3)
    second = ingest_scan(db, run_demo_scan(later), demo_bookmakers(later), DataMode.DEMO, later)
    assert second.new_opportunities == 0
    assert second.new_snapshots == 0
    assert count(db, OpportunityRow) == 9


# ---------------------------------------------------------------------------
# Nýjar stuðlaútgáfur
# ---------------------------------------------------------------------------

def test_new_odds_version_creates_snapshot_and_observation(db):
    ingest(db, BASE())
    later = NOW + timedelta(minutes=10)
    r = ingest(db, scan(pair("Arsenal", "Chelsea", OU25, "2.15", "2.05", now=later), now=later), now=later)
    assert r.new_snapshots == 1  # aðeins stuðullinn sem breyttist
    assert r.unchanged_quotes == 1
    assert r.new_opportunities == 0  # sama tækifæri, nýir stuðlar
    assert r.new_observations == 1
    with db.session() as s:
        prices = s.scalars(
            select(OddsSnapshotRow.price).where(OddsSnapshotRow.outcome == "OVER").order_by(OddsSnapshotRow.id)
        ).all()
        assert prices == [D("2.10"), D("2.15")]
        opp = s.scalars(select(OpportunityRow)).one()
        assert opp.odds_a == D("2.15")
        assert opp.peak_margin_pct > D("4.9")
        assert len(opp.observations) == 2


def test_price_back_and_forth_creates_three_versions(db):
    for i, price in enumerate(["2.10", "2.20", "2.10"]):
        t = NOW + timedelta(minutes=i)
        ingest(db, scan(pair("Arsenal", "Chelsea", OU25, price, "2.05", now=t), now=t), now=t)
    with db.session() as s:
        prices = s.scalars(
            select(OddsSnapshotRow.price).where(OddsSnapshotRow.outcome == "OVER").order_by(OddsSnapshotRow.id)
        ).all()
    assert prices == [D("2.10"), D("2.20"), D("2.10")]


def test_peak_margin_kept_when_margin_drops(db):
    ingest(db, scan(pair("Arsenal", "Chelsea", OU25, "2.30", "2.05")))
    later = NOW + timedelta(minutes=5)
    ingest(db, scan(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", now=later), now=later), now=later)
    with db.session() as s:
        opp = s.scalars(select(OpportunityRow)).one()
        assert opp.peak_margin_pct > opp.theoretical_margin_pct


# ---------------------------------------------------------------------------
# Auðkenni tækifæra
# ---------------------------------------------------------------------------

def _key(match="M1", market=OU25, a=O, b=U, bm_a="coolbet", bm_b="bet365"):
    return make_opportunity_key(match, market, a, b, bm_a, bm_b)


def test_key_is_stable_and_deterministic():
    assert _key() == _key()
    assert len(_key()) == 32


@pytest.mark.parametrize(
    "changed",
    [
        dict(match="M2"),
        dict(market=over_under("3.5")),
        dict(market=over_under("2.5", period=Period.FIRST_HALF)),
        dict(market=over_under("2.5", total_type=TotalType.ASIAN)),
        dict(a=U, b=O),
        dict(bm_a="epicbet"),
        dict(bm_b="epicbet"),
        dict(market=btts(), a=Y, b=N),
    ],
)
def test_key_changes_with_each_component(changed):
    assert _key(**changed) != _key()


def test_key_ignores_odds():
    r1 = scan(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"))
    r2 = scan(pair("Arsenal", "Chelsea", OU25, "2.40", "1.95"))
    assert opportunity_key(r1.arbitrage[0]) == opportunity_key(r2.arbitrage[0])


def test_different_bookmaker_pair_is_new_opportunity(db):
    ingest(db, BASE())
    later = NOW + timedelta(minutes=5)
    r = ingest(db, scan(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", bm_b="epicbet", now=later), now=later),
               now=later)
    assert r.new_opportunities == 1
    assert r.deactivated == 1
    assert count(db, OpportunityRow) == 2


# ---------------------------------------------------------------------------
# Fyrsta og síðasta birting, virkni
# ---------------------------------------------------------------------------

def test_first_last_seen_and_active_flag(db):
    t0, t1, t2, t3 = (NOW + timedelta(hours=h) for h in (0, 1, 2, 3))
    other = lambda t: pair("Liverpool", "Arsenal", OU15, "1.47", "3.80", now=t)  # noqa: E731
    arsenal = lambda t: pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", now=t)  # noqa: E731

    ingest(db, scan(arsenal(t0), other(t0), now=t0), now=t0)
    ingest(db, scan(arsenal(t1), other(t1), now=t1), now=t1)
    r = ingest(db, scan(other(t2), now=t2), now=t2)  # Arsenal-tækifærið horfið
    assert r.deactivated == 1
    with db.session() as s:
        opp = s.scalars(select(OpportunityRow).join(MatchRow).where(MatchRow.home_team_id == "ARS")).one()
        assert (opp.first_seen_at, opp.last_seen_at, opp.is_active) == (t0, t1, False)

    r = ingest(db, scan(arsenal(t3), other(t3), now=t3), now=t3)  # birtist aftur
    assert r.new_opportunities == 0
    with db.session() as s:
        opp = s.scalars(select(OpportunityRow).join(MatchRow).where(MatchRow.home_team_id == "ARS")).one()
        assert (opp.first_seen_at, opp.last_seen_at, opp.is_active) == (t0, t3, True)


def test_out_of_order_ingest_keeps_first_and_last(db):
    later, earlier = NOW + timedelta(hours=1), NOW - timedelta(hours=1)
    ingest(db, BASE(), now=NOW)
    ingest(db, BASE(later), now=later)
    ingest(db, BASE(earlier), now=earlier)
    with db.session() as s:
        opp = s.scalars(select(OpportunityRow)).one()
        assert (opp.first_seen_at, opp.last_seen_at) == (earlier, later)


# ---------------------------------------------------------------------------
# Talning einstakra tækifæra og greining
# ---------------------------------------------------------------------------

def _five_opportunities(now=NOW):
    return scan(
        pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", now=now),               # 3,73%
        pair("Liverpool", "Tottenham", btts(), "2.20", "1.95", now=now),          # 3,37%
        pair("Newcastle", "Aston Villa", OU15, "1.47", "3.80", now=now),          # 6,00%
        pair("West Ham", "Brighton", OU25, "2.06", "2.04", now=now),              # 2,50%
        pair("Manchester City", "Manchestr United", OU25, "2.02", "2.00", now=now),  # 0,50%, fuzzy
        now=now,
    )


def test_unique_count_not_inflated_by_repeats(db):
    for h in range(5):
        t = NOW + timedelta(minutes=h)
        ingest(db, _five_opportunities(t), now=t)
    items = load_history(db, LIVE, NOW + timedelta(minutes=5), timedelta(hours=24))
    assert len(items) == 5
    assert summarize_history(items).unique_count == 5
    assert count(db, OpportunityObservationRow) == 5


def test_threshold_analysis(db):
    ingest(db, _five_opportunities())
    s = summarize_history(load_history(db, LIVE, NOW, timedelta(hours=24)))
    assert s.unique_count == 5
    assert (s.over_2, s.over_3, s.over_5) == (4, 3, 1)
    assert round(s.max_margin_pct, 2) == D("6.00")
    margins = [D(1) / (D(1) / D(a) + D(1) / D(b)) * 100 - 100
               for a, b in [("2.10", "2.05"), ("2.20", "1.95"), ("1.47", "3.80"), ("2.06", "2.04"), ("2.02", "2.00")]]
    assert s.avg_margin_pct == sum(margins) / 5
    assert s.meets_100k_count == 4
    assert s.unconfirmed_count == 1  # fuzzy-samsvörun


def test_threshold_is_strictly_above():
    from scanner.analytics import HistoricalOpportunity

    def item(m):
        return HistoricalOpportunity(
            key=str(m), mode="LIVE", match="x", competition="c", kickoff_utc=NOW, market="m",
            outcome_a="OVER", outcome_b="UNDER", bookmaker_a="a", bookmaker_b="b", odds_a=D(2), odds_b=D(2),
            margin_pct=D(m), peak_margin_pct=D(m), real_margin_pct=None, worst_case_profit=None,
            total_stake=D(100_000), max_feasible_total=None, status="Óstaðfest", data_basis="DEMO",
            first_seen_at=NOW, last_seen_at=NOW, is_active=True, observations=1,
        )

    s = summarize_history([item("2"), item("3"), item("5"), item("5.0001")])
    assert (s.over_2, s.over_3, s.over_5) == (3, 2, 1)


def test_meets_count_only_at_100k(db):
    ingest(db, run(*pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"), total_stake=50_000))
    s = summarize_history(load_history(db, LIVE, NOW, None))
    assert s.meets_100k_count == 0


def test_period_filters(db):
    for days_ago in (0, 3, 20, 40):
        t = NOW - timedelta(days=days_ago)
        home = {0: "Arsenal", 3: "Liverpool", 20: "Newcastle", 40: "West Ham"}[days_ago]
        ingest(db, scan(pair(home, "Chelsea", OU25, "2.10", "2.05", now=t), now=t), now=t)
    n = lambda p: len(load_history(db, LIVE, NOW, p))  # noqa: E731
    assert n(timedelta(hours=24)) == 1
    assert n(timedelta(days=7)) == 2
    assert n(timedelta(days=30)) == 3
    assert n(None) == 4


def test_period_boundary_inclusive(db):
    edge = NOW - timedelta(hours=24)
    ingest(db, scan(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", now=edge), now=edge), now=edge)
    just_before = edge - timedelta(seconds=1)
    ingest(db, scan(pair("Liverpool", "Chelsea", OU25, "2.10", "2.05", now=just_before), now=just_before),
           now=just_before)
    items = load_history(db, LIVE, NOW, timedelta(hours=24))
    assert [i.match for i in items] == ["Arsenal – Chelsea"]


def test_period_uses_last_seen_not_first_seen(db):
    # Fyrst séð fyrir 10 dögum, síðast séð í dag -> með í 24 klst.
    old = NOW - timedelta(days=10)
    ingest(db, BASE(old), now=old)
    ingest(db, BASE(), now=NOW)
    items = load_history(db, LIVE, NOW, timedelta(hours=24))
    assert len(items) == 1
    # ...en ekki talið sem NÝTT tækifæri innan 24 klst. eða 7 daga
    assert sum(n for _, n in daily_new_counts(items, NOW, timedelta(hours=24))) == 0
    assert sum(n for _, n in daily_new_counts(items, NOW, timedelta(days=7))) == 0
    assert sum(n for _, n in daily_new_counts(load_history(db, LIVE, NOW, timedelta(days=30)),
                                              NOW, timedelta(days=30))) == 1


# ---------------------------------------------------------------------------
# Tímaraðir og tímaásar
# ---------------------------------------------------------------------------

def test_time_buckets_hourly_and_daily():
    hourly = time_buckets(NOW, NOW - timedelta(hours=24), hourly=True)
    assert len(hourly) == 25  # 12:00 í gær ... 12:00 í dag
    assert hourly[0].label == "31.10. 12:00" and hourly[-1].label == "01.11. 12:00"
    assert all(b.start.minute == b.start.second == b.start.microsecond == 0 for b in hourly)
    daily = time_buckets(NOW, NOW - timedelta(days=7), hourly=False)
    assert [b.label for b in daily][:2] == ["25.10.2026", "26.10.2026"]
    assert daily[-1].label == "01.11.2026"
    assert len(daily) == 8
    assert len(time_buckets(NOW, NOW - timedelta(days=30), hourly=False)) == 31


def test_is_hourly():
    assert is_hourly(timedelta(hours=24))
    assert not is_hourly(timedelta(days=7))
    assert not is_hourly(timedelta(days=30))
    assert not is_hourly(None)


@pytest.mark.parametrize("period,expected_days", [(timedelta(hours=24), 2), (timedelta(days=7), 8),
                                                  (timedelta(days=30), 31)])
def test_daily_new_counts_cover_whole_window(db, period, expected_days):
    ingest(db, BASE())
    items = load_history(db, LIVE, NOW, period)
    counts = daily_new_counts(items, NOW, period)
    assert len(counts) == expected_days
    assert all(isinstance(label, str) and len(label) == 10 for label, _ in counts)  # 'dd.mm.yyyy'
    assert counts[-1] == ("01.11.2026", 1)
    assert sum(n for _, n in counts) == 1


def test_daily_new_counts_zero_days(db):
    for d in (0, 0, 2):
        t = NOW - timedelta(days=d)
        ingest(db, scan(pair("Arsenal" if d == 0 else "Liverpool", "Chelsea", OU25, "2.10", "2.05", now=t), now=t),
               now=t)
    counts = daily_new_counts(load_history(db, LIVE, NOW, timedelta(days=7)), NOW, timedelta(days=7))
    assert counts[-3:] == [("30.10.2026", 1), ("31.10.2026", 0), ("01.11.2026", 1)]


def test_margin_series_24h_hourly(db):
    ingest(db, scan(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", now=NOW - timedelta(hours=5)),
                    now=NOW - timedelta(hours=5)), now=NOW - timedelta(hours=5))
    ingest(db, BASE(), now=NOW)  # óbreytt, sást aftur núna
    points = margin_series(load_history(db, LIVE, NOW, timedelta(hours=24)), NOW, timedelta(hours=24))
    assert len(points) == 25
    alive = [p for p in points if p.count]
    assert [p.bucket.label for p in alive][0] == "01.11. 07:00"
    assert len(alive) == 6  # 07:00 ... 12:00
    assert all(p.avg_margin_pct == p.max_margin_pct for p in alive)
    assert all(p.avg_margin_pct is None for p in points if not p.count)  # rof, ekki 0%


def test_margin_series_uses_margin_at_that_time(db):
    t0 = NOW - timedelta(days=3)
    ingest(db, scan(pair("Arsenal", "Chelsea", OU25, "2.30", "2.05", now=t0), now=t0), now=t0)
    ingest(db, BASE(), now=NOW)  # framlegð lækkaði í dag
    items = load_history(db, LIVE, NOW, timedelta(days=7))
    points = {p.bucket.label: p for p in margin_series(items, NOW, timedelta(days=7))}
    assert points["29.10.2026"].max_margin_pct > D(8)  # 2,30 / 2,05
    assert round(points["01.11.2026"].max_margin_pct, 2) == D("3.73")  # 2,10 / 2,05


def test_margin_series_includes_opportunity_changed_before_window(db):
    old = NOW - timedelta(days=10)
    ingest(db, BASE(old), now=old)
    ingest(db, BASE(), now=NOW)  # óbreyttir stuðlar -> engin ný athugun í dag
    items = load_history(db, LIVE, NOW, timedelta(hours=24))
    points = margin_series(items, NOW, timedelta(hours=24))
    assert all(p.count == 1 for p in points)  # var til allan sólarhringinn
    assert round(points[-1].max_margin_pct, 2) == D("3.73")


def test_bar_chart_uses_date_categories():
    from scanner.history_view import counts_figure

    fig = counts_figure([("31.10.2026", 0), ("01.11.2026", 3)])
    assert fig.layout.xaxis.type == "category"
    assert list(fig.data[0].x) == ["31.10.2026", "01.11.2026"]
    assert list(fig.layout.xaxis.categoryarray) == ["31.10.2026", "01.11.2026"]


@pytest.mark.parametrize(
    "period,first,last,n",
    [
        (timedelta(hours=24), "31.10. 12:00", "01.11. 12:00", 25),
        (timedelta(days=7), "25.10.2026", "01.11.2026", 8),
        (timedelta(days=30), "02.10.2026", "01.11.2026", 31),
    ],
)
def test_charts_have_readable_axes(db, period, first, last, n):
    from scanner.history_view import counts_figure, margin_figure

    ingest(db, BASE())
    items = load_history(db, LIVE, NOW, period)
    line = margin_figure(margin_series(items, NOW, period), is_hourly(period))
    bar = counts_figure(daily_new_counts(items, NOW, period))
    ax = line.layout.xaxis
    assert ax.type == "category"  # engin samfelld sekúndu-/millisekúnduskipting
    labels = list(line.data[0].x)
    assert (labels[0], labels[-1], len(labels)) == (first, last, n)
    assert all(":" not in label or label.endswith(":00") for label in labels)  # heilar klukkustundir
    for fig in (line, bar):
        ticks = list(fig.layout.xaxis.tickvals)
        assert len(ticks) <= 13  # ekki ofhlaðið
        assert ticks[-1] == fig.layout.xaxis.categoryarray[-1]  # nýjasti dagur/tími alltaf merktur
        assert fig.layout.xaxis.tickangle == 0
    assert bar.layout.xaxis.type == "category"
    assert list(bar.data[0].x)[-1] == "01.11.2026"


# ---------------------------------------------------------------------------
# DEMO og LIVE aðskilin
# ---------------------------------------------------------------------------

def test_demo_and_live_separated(db):
    live = scan(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05"))
    demo = scan(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", source=DEMO))
    ingest(db, live, mode=DataMode.LIVE)
    ingest(db, demo, mode=DataMode.DEMO)
    # Sami leikur skráður sitt í hvoru lagi
    assert count(db, MatchRow) == 2
    assert count(db, OpportunityRow) == 2
    live_items = load_history(db, DataMode.LIVE, NOW, None)
    demo_items = load_history(db, DataMode.DEMO, NOW, None)
    assert [i.mode for i in live_items] == ["LIVE"]
    assert [i.mode for i in demo_items] == ["DEMO"]


def test_demo_and_live_separated_in_every_table(db):
    from sqlalchemy import text

    live_scan = _five_opportunities()
    demo_scan = scan(
        pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", source=DEMO),       # sami leikur og í LIVE
        pair("Newcastle", "Aston Villa", OU15, "1.47", "3.80", source=DEMO),
    )
    ingest(db, live_scan, mode=DataMode.LIVE)
    ingest(db, demo_scan, mode=DataMode.DEMO)
    with db.engine.connect() as c:
        def by_mode(table):
            return dict(c.execute(text(f"SELECT mode, COUNT(*) FROM {table} GROUP BY mode")).all())

        assert by_mode("matches") == {"LIVE": 5, "DEMO": 2}
        assert by_mode("odds_snapshots") == {"LIVE": 10, "DEMO": 4}
        assert by_mode("opportunities") == {"LIVE": 5, "DEMO": 2}
        # Sami leikur er tvær aðskildar línur – aldrei sameiginlegur
        same = c.execute(text(
            "SELECT COUNT(DISTINCT id), COUNT(DISTINCT mode) FROM matches WHERE home_team_id='ARS'"
        )).one()
        assert tuple(same) == (2, 2)
        # Engin LIVE-athugun vísar á DEMO-tækifæri eða öfugt
        mixed = c.execute(text(
            "SELECT COUNT(*) FROM opportunities o JOIN matches m ON o.match_id = m.id WHERE o.mode != m.mode"
        )).scalar()
        assert mixed == 0
        mixed_snap = c.execute(text(
            "SELECT COUNT(*) FROM odds_snapshots s JOIN matches m ON s.match_id = m.id WHERE s.mode != m.mode"
        )).scalar()
        assert mixed_snap == 0

    live_items = load_history(db, DataMode.LIVE, NOW, None)
    demo_items = load_history(db, DataMode.DEMO, NOW, None)
    assert summarize_history(live_items).unique_count == 5
    assert summarize_history(demo_items).unique_count == 2
    assert {i.mode for i in live_items} == {"LIVE"} and {i.mode for i in demo_items} == {"DEMO"}
    assert sum(n for _, n in daily_new_counts(demo_items, NOW, timedelta(days=7))) == 2
    assert max(p.count for p in margin_series(demo_items, NOW, timedelta(days=7))) == 2


def test_demo_data_rejected_as_live(db):
    demo = scan(pair("Arsenal", "Chelsea", OU25, "2.10", "2.05", source=DEMO))
    with pytest.raises(ValueError, match="DEMO og LIVE"):
        ingest(db, demo, mode=DataMode.LIVE)
    assert count(db, OpportunityRow) == 0
    assert count(db, OddsSnapshotRow) == 0  # ekkert skrifað að hluta


def test_live_data_rejected_as_demo(db):
    with pytest.raises(ValueError):
        ingest(db, BASE(), mode=DataMode.DEMO)


def test_deactivation_only_within_mode(db):
    ingest(db, BASE(), mode=DataMode.LIVE)
    empty_demo = scan(pair("Arsenal", "Chelsea", OU25, "1.90", "1.90", source=DEMO))
    r = ingest(db, empty_demo, mode=DataMode.DEMO)
    assert r.deactivated == 0
    assert load_history(db, DataMode.LIVE, NOW, None)[0].is_active


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def test_csv_export(db):
    ingest(db, _five_opportunities())
    items = load_history(db, LIVE, NOW, None)
    data = to_csv(items)
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM fyrir Excel
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 5
    arsenal = next(r for r in rows if r["Leikur"] == "Arsenal – Chelsea")
    assert arsenal["Stuðull A"] == "2,1"
    assert arsenal["Stuðull B"] == "2,05"
    assert arsenal["Lægsti hagnaður ISK"] == "3734,1"
    assert arsenal["Heildarfjárhæð ISK"] == "100000"
    assert arsenal["Framlegð %"] == "3,7349"
    assert arsenal["Hamur"] == "LIVE"
    assert arsenal["Virkt"] == "Já"
    assert arsenal["Fyrst séð (UTC)"] == "2026-11-01 12:00:00"
    assert "Auðkenni" in rows[0] and len(arsenal["Auðkenni"]) == 32


def test_csv_empty():
    data = to_csv([])
    lines = data.decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("Auðkenni;")
