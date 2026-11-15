"""Lokaúttekt á áfanga 6.

Niðurstöður viðmótsins eru bornar saman við ÓHÁÐA endurútreikninga
(bein formúla / assess_feasibility), ekki sömu föll og viðmótið notar.
"""

from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from scanner.dashboard import UNCONFIRMED_PROFIT_NOTE, profit_note
from scanner.demo import run_demo_scan
from scanner.demo_data import DEMO_FX_RATES, demo_bookmakers
from scanner.feasibility import assess_feasibility
from scanner.formatting import format_isk
from scanner.risk import RiskStatus
from tests.test_app import (
    APP_PATH,
    _count,
    alert_toasts,
    dash,
    detailed,
    high,
    kpis,
    opp_table,
    select,
    unread_alerts,
)

D = Decimal


@pytest.fixture
def app() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception
    return at


def margin(a: Decimal, b: Decimal) -> Decimal:
    return (D(1) / (D(1) / a + D(1) / b) - 1) * 100


def card_titles(at) -> list[str]:
    return [m.value.removeprefix("#### 🧪 DEMO · ") for m in high(at).markdown if m.value.startswith("#### ")]


# ---------------------------------------------------------------------------
# 1. 5%+ flipinn sýnir NÁKVÆMLEGA tækifærin sem ná 5%
# ---------------------------------------------------------------------------

def test_1_high_margin_tab_exact(app):
    now = app.session_state["data_refreshed_at"]
    report = run_demo_scan(now, anchor=app.session_state["demo_anchor"])
    expected = {f"{o.match.label} · {o.market.label}" for o in report.arbitrage
                if margin(o.quote_a.price, o.quote_b.price) >= 5}
    assert set(card_titles(app)) == expected
    assert len(card_titles(app)) == len(expected) == 2  # engin tvítekning
    # Öll tækifæri undir 5% í töflunni eru EKKI í flipanum
    df = opp_table(app)
    below = {f"{a} · {b}" for a, b, m in zip(df["Leikur"], df["Markaður"], df["Framlegð"])
             if D(m.rstrip("%").replace(",", ".")) < 5}
    assert below and not below & set(card_titles(app))


def test_1_high_margin_boundary():
    from tests.helpers import event, run
    from scanner.dashboard import DashboardFilters, build_rows, high_margin_rows
    from scanner.models import Outcome, over_under
    from tests.helpers import NOW

    ou = over_under("2.5")
    report = run(
        event("coolbet", "Arsenal", "Chelsea", (ou, Outcome.OVER, "2.10")),       # 2,10/2,10 = 5,00%
        event("bet365", "Arsenal", "Chelsea", (ou, Outcome.UNDER, "2.10")),
        event("coolbet", "Liverpool", "Chelsea", (ou, Outcome.OVER, "2.10")),     # 2,10/2,09 = 4,75%
        event("bet365", "Liverpool", "Chelsea", (ou, Outcome.UNDER, "2.09")),
    )
    rows = high_margin_rows(build_rows(report, NOW), DashboardFilters(), NOW)
    assert [r.opportunity.match.label for r in rows] == ["Arsenal – Chelsea"]


# ---------------------------------------------------------------------------
# 2. Lesnar viðvaranir haldast lesnar eftir endurræsingu
# ---------------------------------------------------------------------------

def test_2_read_alerts_stay_read_after_restart(app):
    assert len(unread_alerts(app)) == 2
    app.button(key="alerts_mark_read").click().run()
    assert unread_alerts(app) == []

    restarted = AppTest.from_file(APP_PATH, default_timeout=60).run()  # ný Streamlit-lota
    assert not restarted.exception
    assert unread_alerts(restarted) == []
    assert len([i for i in restarted.main.info if i.icon == "✔️"]) == 2
    assert any(e.label.startswith("🔔 Viðvaranir (0 ólesnar)") for e in restarted.expander)
    assert not [b for b in restarted.button if b.key == "alerts_mark_read"]  # ekkert að merkja


# ---------------------------------------------------------------------------
# 3. Engin ný viðvörun við endurhleðslu
# ---------------------------------------------------------------------------

def test_3_no_new_alert_on_reload(app, temp_database_url):
    from scanner.db import AlertRow

    assert _count(temp_database_url, AlertRow) == 2
    for _ in range(5):
        app.run()  # endurhleðsla
        assert alert_toasts(app) == []
    app.button(key="refresh_data").click().run()
    assert alert_toasts(app) == []
    for _ in range(2):
        again = AppTest.from_file(APP_PATH, default_timeout=60).run()  # ný lota / endurræsing
        assert alert_toasts(again) == []
    assert _count(temp_database_url, AlertRow) == 2


# ---------------------------------------------------------------------------
# 4. 100.000 kr. -> 50.000 kr.: allar hagnaðartölur uppfærast rétt
# ---------------------------------------------------------------------------

def _expected_profits(app, total) -> dict[str, Decimal]:
    now = app.session_state["data_refreshed_at"]
    report = run_demo_scan(now, anchor=app.session_state["demo_anchor"])
    books = demo_bookmakers(now)
    out = {}
    for o in report.arbitrage:
        f = assess_feasibility(o.quote_a.price, o.quote_b.price, books[o.bookmaker_a.id].limits,
                               books[o.bookmaker_b.id].limits, total, now=now, fx_rates=DEMO_FX_RATES)
        out[f"{o.match.label} · {o.market.label}"] = f.worst_case_profit
    return out


def _table_profits(at) -> dict[str, str]:
    df = opp_table(at)
    return {f"{a} · {b}": p.removeprefix("≈ ") for a, b, p in zip(df["Leikur"], df["Markaður"], df["Lægsti hagnaður"])}


def test_4_change_total_to_50k(app):
    p100 = _table_profits(app)
    exp100 = _expected_profits(app, 100_000)
    assert p100 == {k: format_isk(v, 2) for k, v in exp100.items()}

    app.number_input(key="scan_total").set_value(50_000).run()
    assert not app.exception
    exp50 = _expected_profits(app, 50_000)
    p50 = _table_profits(app)
    # Hver einasta lína í töflunni passar við óháðan útreikning við 50.000 kr.
    assert p50 == {k: format_isk(v, 2) for k, v in exp50.items()}
    # Hagnaður u.þ.b. helmingast (frávik aðeins vegna námundunar í heilar krónur/skref)
    for k in exp50:
        assert abs(exp50[k] * 2 - exp100[k]) < D(30), k

    # Ítarleg sýn: fjárhæðir og heildarfjárhæð
    df = opp_table(detailed(app))
    assert set(df["Heildarfjárhæð"]) == {"50.000 kr."}
    # Yfirlitsreitur: hæsti nettóhagnaður við 50.000 kr.
    assert kpis(app)["Hæsti áætlaði nettóhagnaður"] == format_isk(max(exp50.values()), 2)
    # 5%+ spjöldin
    card_profits = [m.value for m in high(app).metric if m.label == "Lægsti nettóhagnaður"]
    titles = card_titles(app)
    assert card_profits == [format_isk(exp50[t], 2) for t in titles]
    assert all("50.000 kr." in m.value for m in high(app).markdown if m.value.startswith("Heildarfjárhæð"))
    # Upplýsingaspjald: hagnaður í báðum úrslitum
    select(app, "Bayern München")
    side_table = dash(app).table[0].value
    now = app.session_state["data_refreshed_at"]
    books = demo_bookmakers(now)
    f = assess_feasibility("1.47", "3.80", books["coolbet"].limits, books["epicbet"].limits, 50_000,
                           now=now, fx_rates=DEMO_FX_RATES)
    assert list(side_table["Hagnaður ef vinnur"]) == [format_isk(f.profit_if_a, 2), format_isk(f.profit_if_b, 2)]
    assert list(side_table["Fjárhæð"]) == [format_isk(f.side_a.stake), format_isk(f.side_b.stake)]


def test_4_history_and_alerts_stay_at_100k(app, temp_database_url):
    """Sagan og viðvaranir miðast viljandi við 100.000 kr. svo þær séu sambærilegar."""
    from scanner.db import Database, OpportunityRow
    from sqlalchemy import select as sql_select

    app.number_input(key="scan_total").set_value(50_000).run()
    db = Database(temp_database_url)
    with db.session() as s:
        assert {r.total_stake for r in s.scalars(sql_select(OpportunityRow))} == {D(100_000)}
    db.dispose()


# ---------------------------------------------------------------------------
# 5. Óstaðfest/úrelt tækifæri aldrei sýnd sem staðfestur hagnaður
# ---------------------------------------------------------------------------

def test_5_profit_note_logic():
    class R:  # lágmarks staðgengill fyrir Row
        def __init__(self, s):
            self.risk = type("Risk", (), {"status": s})()

    assert profit_note(R(RiskStatus.MEETS)) is None
    for s in RiskStatus:
        if s is not RiskStatus.MEETS:
            assert profit_note(R(s)) == UNCONFIRMED_PROFIT_NOTE


def test_5_table_marks_unconfirmed_profit(app):
    df = opp_table(app)
    for status, profit in zip(df["Staða"], df["Lægsti hagnaður"]):
        if status.startswith("🟢"):
            assert not profit.startswith("≈")
        else:
            assert profit.startswith("≈ "), (status, profit)
    assert any(not s.startswith("🟢") for s in df["Staða"])  # prófunin nær yfir óstaðfest/úrelt


def test_5_kpi_profit_shows_status(app):
    # Hæsti nettóhagnaður (6.463,30 kr.) kemur úr óstaðfestu tækifæri -> fyrirvari sýndur
    assert kpis(app)["Hæsti áætlaði nettóhagnaður"] == "6.463,30 kr."
    caption = next(c.value for c in app.main.caption if c.value.startswith("Hæsti áætlaði nettóhagnaður:"))
    assert "🟡 Óstaðfest" in caption and "ekki staðfestur hagnaður" in caption


def test_5_details_panel_never_green_for_unconfirmed_or_stale(app):
    for match, icon in (("Eintracht Frankfurt", "🟡"), ("Napoli", "🔴"), ("Lazio", "🔴")):
        select(app, match)
        tab = dash(app)
        assert not tab.success, match
        assert any(icon in b.value for b in [*tab.warning, *tab.error]), match
    select(app, "Bayern München")  # Uppfyllir skráð skilyrði
    assert any("🟢 Uppfyllir skráð skilyrði" in s.value for s in dash(app).success)


def test_5_high_margin_cards(app):
    tab = high(app)
    # Frankfurt (óstaðfest): staða sýnd og fyrirvari við hagnaðartölur
    assert any("🟡 Óstaðfest" in w.value for w in tab.warning)
    assert any("ekki staðfestur hagnaður (Óstaðfest)" in c.value for c in tab.caption)
    # Bayern (uppfyllir): enginn fyrirvari um óstaðfestan hagnað
    notes = [c.value for c in tab.caption if "ekki staðfestur hagnaður" in c.value]
    assert len(notes) == 1


def test_5_no_status_or_label_claims_safety(app):
    texts = [e.value for e in [*app.markdown, *app.caption, *app.success, *app.warning, *app.error, *app.info]]
    for t in texts:
        low = t.lower()
        assert "áhættulaus" not in low or "ekkert tækifæri er öruggt eða áhættulaust" in low
        assert "tryggður hagnaður" not in low and "staðfestur hagnaður" not in low.replace("ekki staðfestur hagnaður", "")
