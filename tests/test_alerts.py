"""Viðvörunarkerfi (áfangi 6)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from scanner.alerts import DEFAULT_ALERT_THRESHOLD, generate_alerts, list_alerts, mark_all_read
from scanner.alerts_view import alert_text
from scanner.db import AlertRow, Database, DataMode, OpportunityRow
from scanner.demo import run_demo_scan
from scanner.demo_data import default_anchor, demo_bookmakers
from scanner.history import ingest_scan, opportunity_key
from scanner.models import DataSource, Outcome, over_under
from tests.helpers import BOOKS, NOW, event, run

D = Decimal
OU25 = over_under("2.5")
DEMO_SRC = DataSource(name="DEMO-próf", kind="DEMO")


@pytest.fixture
def db(temp_database_url):
    database = Database(temp_database_url)
    yield database
    database.dispose()


def count_alerts(db) -> int:
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(AlertRow))


def demo_cycle(db, now=NOW, threshold=DEFAULT_ALERT_THRESHOLD, anchor=None):
    """Það sama og app.py gerir: leit -> vista sögu -> búa til viðvaranir."""
    anchor = anchor or default_anchor(NOW)
    report = run_demo_scan(now, anchor=anchor)
    ingest_scan(db, report, demo_bookmakers(now), DataMode.DEMO, now)
    return report, generate_alerts(db, report, DataMode.DEMO, now, threshold)


def live_cycle(db, *pairs, now=NOW, threshold=DEFAULT_ALERT_THRESHOLD):
    events = []
    for home, away, oa, ob in pairs:
        events += [event("coolbet", home, away, (OU25, Outcome.OVER, oa), now=now),
                   event("bet365", home, away, (OU25, Outcome.UNDER, ob), now=now)]
    report = run(*events, now=now)
    ingest_scan(db, report, BOOKS, DataMode.LIVE, now)
    return report, generate_alerts(db, report, DataMode.LIVE, now, threshold)


def test_default_threshold_is_5():
    assert DEFAULT_ALERT_THRESHOLD == D(5)


def test_alerts_created_for_opportunities_at_threshold(db):
    report, new = demo_cycle(db)
    expected = [o for o in report.arbitrage if o.margin_pct >= 5]
    assert len(new) == len(expected) == 2
    assert {a.match_label for a in new} == {"Eintracht Frankfurt – VfB Stuttgart", "Bayern München – Borussia Dortmund"}


def test_alert_contents(db):
    _, new = demo_cycle(db)
    a = next(x for x in new if x.match_label.startswith("Bayern"))
    assert a.market_label == "Over/Under 1.5"
    assert (a.bookmaker_a, a.outcome_a, a.odds_a) == ("Coolbet", "Over", D("1.47"))
    assert (a.bookmaker_b, a.outcome_b, a.odds_b) == ("Epicbet", "Under", D("3.80"))
    assert round(a.margin_pct, 2) == D("6.00")
    assert a.worst_case_profit > 0
    assert a.total_stake == D(100_000)
    assert a.status == "Uppfyllir skráð skilyrði"
    assert a.odds_age_seconds == 30
    assert a.mode == "DEMO" and a.is_demo and not a.is_read
    text = alert_text(a)
    assert text.startswith("🧪 DEMO · ")
    for part in ("Bayern München", "Over/Under 1.5", "1,47", "3,80", "Coolbet", "Epicbet", "6,00%",
                 "Uppfyllir skráð skilyrði", "30 sek.", "lægsti nettóhagnaður"):
        assert part in text


def test_alert_linked_to_opportunity(db):
    report, new = demo_cycle(db)
    with db.session() as s:
        for row in s.scalars(select(AlertRow)):
            opp = s.get(OpportunityRow, row.opportunity_id)
            assert opp.opp_key == row.opp_key
            assert opp.mode == row.mode
    keys = {opportunity_key(o) for o in report.arbitrage if o.margin_pct >= 5}
    assert {a.opp_key for a in new} == keys


def test_no_duplicate_alerts_on_reload(db):
    _, first = demo_cycle(db)
    for minutes in (0, 1, 30, 600):  # endurhleðsla, uppfærsla, endurræsing ...
        _, again = demo_cycle(db, now=NOW + timedelta(minutes=minutes))
        assert again == []
    assert count_alerts(db) == len(first) == 2


def test_threshold_change_only_adds_new(db):
    _, at5 = demo_cycle(db)
    _, at3 = demo_cycle(db, threshold=D(3))
    assert len(at5) == 2
    assert at3 and all(D(3) <= a.margin_pct < D(5) for a in at3)  # aðeins þau sem bættust við
    _, back_to_5 = demo_cycle(db, threshold=D(5))
    _, again_3 = demo_cycle(db, threshold=D(3))
    assert back_to_5 == [] and again_3 == []
    assert count_alerts(db) == len(at5) + len(at3)


def test_no_alerts_below_threshold(db):
    _, new = live_cycle(db, ("Arsenal", "Chelsea", "2.10", "2.05"))  # 3,73%
    assert new == []
    assert count_alerts(db) == 0


def test_odds_change_on_same_opportunity_does_not_realert(db):
    _, first = live_cycle(db, ("Arsenal", "Chelsea", "2.30", "2.05"))
    assert len(first) == 1
    later = NOW + timedelta(minutes=5)
    _, again = live_cycle(db, ("Arsenal", "Chelsea", "2.40", "2.05"), now=later)
    assert again == []


def test_demo_live_alerts_separated(db):
    demo_cycle(db)
    _, live_new = live_cycle(db, ("Arsenal", "Chelsea", "2.30", "2.05"))
    assert len(live_new) == 1 and live_new[0].mode == "LIVE" and not live_new[0].is_demo
    assert not alert_text(live_new[0]).startswith("🧪")
    assert {a.mode for a in list_alerts(db, DataMode.DEMO)} == {"DEMO"}
    assert {a.mode for a in list_alerts(db, DataMode.LIVE)} == {"LIVE"}
    assert len(list_alerts(db, DataMode.DEMO)) == 2 and len(list_alerts(db, DataMode.LIVE)) == 1


def test_demo_report_rejected_as_live_alerts(db):
    report = run_demo_scan(NOW)
    ingest_scan(db, report, demo_bookmakers(NOW), DataMode.DEMO, NOW)
    with pytest.raises(ValueError, match="DEMO og LIVE"):
        generate_alerts(db, report, DataMode.LIVE, NOW)
    assert count_alerts(db) == 0


def test_alert_requires_saved_opportunity(db):
    report = run(event("coolbet", "Arsenal", "Chelsea", (OU25, Outcome.OVER, "2.30")),
                 event("bet365", "Arsenal", "Chelsea", (OU25, Outcome.UNDER, "2.05")))
    with pytest.raises(LookupError):
        generate_alerts(db, report, DataMode.LIVE, NOW)


def test_mark_read_and_unread_listing(db):
    demo_cycle(db)
    assert len(list_alerts(db, DataMode.DEMO, only_unread=True)) == 2
    assert mark_all_read(db, DataMode.DEMO) == 2
    assert list_alerts(db, DataMode.DEMO, only_unread=True) == []
    assert all(a.is_read for a in list_alerts(db, DataMode.DEMO))
    assert mark_all_read(db, DataMode.DEMO) == 0


def test_alerts_survive_restart(db, temp_database_url):
    demo_cycle(db)
    db.dispose()
    reopened = Database(temp_database_url)
    _, again = demo_cycle(reopened, now=NOW + timedelta(hours=1))
    assert again == [] and len(list_alerts(reopened, DataMode.DEMO)) == 2
    reopened.dispose()
