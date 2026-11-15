"""Streamlit-prófanir: keyra app.py í AppTest án vafra."""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
KPI_LABELS = ["Fjöldi leikja", "Arbitrage-tækifæri", "Fjöldi ≥ 5%", "Hæsta framlegð",
              "Hæsti áætlaði nettóhagnaður", "Síðasta gagnauppfærsla"]
DETAILED_COLUMNS = ["Leikur", "Keppni", "Upphaf (UTC)", "Markaður", "Veðmálasíður", "Stuðlar", "Framlegð",
                    "Fjárhæð A", "Fjárhæð B", "Heildarfjárhæð", "Lægsti hagnaður", "Hæsta mögulega fjárhæð",
                    "Staða", "Aldur stuðla", "Uppspretta"]
COMPACT_COLUMNS = ["Leikur", "Markaður", "Framlegð", "Lægsti hagnaður", "Staða", "Aldur stuðla"]


@pytest.fixture
def app() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception
    return at


# Flipar: 0 Mælaborð, 1 5%+, 2 Reiknivél, 3 Saga, 4 Veðmálamörk
def dash(at):
    return at.tabs[0]


def high(at):
    return at.tabs[1]


def calc(at):
    return at.tabs[2]


def hist(at):
    return at.tabs[3]


def limits_tab(at):
    return at.tabs[4]


def kpis(at: AppTest) -> dict[str, str]:
    """Yfirlitsreitirnir efst (fyrstu 6 metric-in, fyrir ofan flipana)."""
    values = {m.label: m.value for m in at.metric[:6]}
    assert list(values) == KPI_LABELS
    return values


def metrics(block) -> dict[str, str]:
    return {m.label: m.value for m in block.metric}


def opp_table(at: AppTest):
    return dash(at).dataframe[0].value


def detailed(at: AppTest) -> AppTest:
    at.radio(key="table_view").set_value("Ítarleg").run()
    return at


def opportunity_count(at: AppTest) -> int:
    for md in dash(at).markdown:
        if "tækifæri passa við síurnar" in md.value:
            return int(md.value.split("**")[1])
    raise AssertionError("Fjöldi tækifæra fannst ekki")


def set_margin(at: AppTest, value: float) -> AppTest:
    at.radio(key="filter_margin_preset").set_value("Sérsniðið").run()
    at.number_input(key="filter_min_margin").set_value(value).run()
    return at


# ---------------------------------------------------------------------------
# Mælaborð og yfirlitstölur
# ---------------------------------------------------------------------------

def test_dashboard_loads(app):
    assert app.title[0].value.endswith("Football Arbitrage Scanner")
    k = kpis(app)
    assert k["Fjöldi leikja"] == "59"
    assert k["Arbitrage-tækifæri"] == "9"
    assert k["Fjöldi ≥ 5%"] == "2"
    assert k["Hæsta framlegð"] == "6,46%"
    assert k["Hæsti áætlaði nettóhagnaður"] == "6.463,30 kr."
    assert k["Síðasta gagnauppfærsla"].endswith("UTC")
    assert any("DEMO" in i.value for i in app.info)
    assert [t.label for t in app.tabs] == ["📊 Mælaborð", "🔥 5%+ tækifæri", "🧮 Reiknivél",
                                           "📈 Saga og greining", "⚙️ Veðmálamörk"]


def test_table_columns_compact_and_detailed(app):
    assert list(opp_table(app).columns) == COMPACT_COLUMNS
    assert list(opp_table(detailed(app)).columns) == DETAILED_COLUMNS


def test_table_sorted_by_margin(app):
    margins = [float(m.rstrip("%").replace(",", ".")) for m in opp_table(app)["Framlegð"]]
    assert margins == sorted(margins, reverse=True)
    assert margins[0] == 6.46


def test_kpis_match_table(app):
    for preset in ("0%", "2%", "3%", "5%"):
        app.radio(key="filter_margin_preset").set_value(preset).run()
        k, df = kpis(app), opp_table(app)
        assert int(k["Arbitrage-tækifæri"]) == len(df) == opportunity_count(app)
        assert k["Hæsta framlegð"] == df["Framlegð"].iloc[0]
        assert int(k["Fjöldi ≥ 5%"]) == sum(float(m.rstrip("%").replace(",", ".")) >= 5 for m in df["Framlegð"])


def test_kpis_follow_competition_filter(app):
    app.multiselect(key="filter_comps").set_value(["Bundesliga"]).run()
    k = kpis(detailed(app))
    df = opp_table(app)
    assert set(df["Keppni"]) == {"Bundesliga"}
    assert int(k["Arbitrage-tækifæri"]) == len(df)
    assert int(k["Fjöldi leikja"]) < 59


def test_data_source_shown(app):
    df = opp_table(detailed(app))
    assert set(df["Uppspretta"]) == {"DEMO"}
    assert any("DEMO-gagnasett" in c.value for c in app.sidebar.caption)


def test_status_and_age_columns(app):
    df = opp_table(app)
    assert set(df["Staða"]) <= {"🟢 Uppfyllir skráð skilyrði", "🟡 Óstaðfest", "🔴 Úrelt",
                                "🔴 Uppfyllir ekki skilyrði", "⛔ Ósambærilegir markaðir"}
    assert all("sek." in a or "mín." in a for a in df["Aldur stuðla"])


def test_scan_total_changes_table(app):
    app.number_input(key="scan_total").set_value(50_000).run()
    assert set(opp_table(detailed(app))["Heildarfjárhæð"]) == {"50.000 kr."}


# ---------------------------------------------------------------------------
# Síur
# ---------------------------------------------------------------------------

def test_margin_presets(app):
    counts = {}
    for preset in ("0%", "2%", "3%", "5%"):
        app.radio(key="filter_margin_preset").set_value(preset).run()
        counts[preset] = opportunity_count(app)
    assert counts["0%"] >= counts["2%"] >= counts["3%"] >= counts["5%"] == 2
    assert opportunity_count(set_margin(app, 6.2)) == 1


def test_filter_market(app):
    app.multiselect(key="filter_markets").set_value(["BTTS (Yes/No)"]).run()
    assert set(opp_table(app)["Markaður"]) == {"BTTS (Yes/No)"}


def test_filter_bookmakers(app):
    app.multiselect(key="filter_bookmakers").set_value(["Coolbet", "Epicbet"]).run()
    df = opp_table(detailed(app))
    assert len(df) > 0 and not any("bet365" in s for s in df["Veðmálasíður"])


def test_filter_status(app):
    app.multiselect(key="filter_statuses").set_value(["Uppfyllir skráð skilyrði"]).run()
    df = opp_table(app)
    assert len(df) > 0 and set(df["Staða"]) == {"🟢 Uppfyllir skráð skilyrði"}
    assert int(kpis(app)["Arbitrage-tækifæri"]) == len(df)


def test_filter_kickoff_and_odds_age(app):
    app.selectbox(key="filter_odds_age").set_value("1 mín.").run()
    with_age = opportunity_count(app)
    assert 0 < with_age < 9
    app.selectbox(key="filter_kickoff").set_value("Næstu 6 klst.").run()
    assert not app.exception  # DEMO-leikir hefjast á morgun -> líklega engir innan 6 klst.


def test_reset_filters(app):
    app.radio(key="filter_margin_preset").set_value("5%").run()
    app.multiselect(key="filter_comps").set_value(["Bundesliga"]).run()
    app.multiselect(key="filter_markets").set_value(["Over/Under 1.5"]).run()
    app.multiselect(key="filter_bookmakers").set_value(["Coolbet", "Epicbet"]).run()
    app.selectbox(key="filter_kickoff").set_value("Næstu 24 klst.").run()
    app.selectbox(key="filter_odds_age").set_value("1 mín.").run()
    app.multiselect(key="filter_statuses").set_value(["Óstaðfest"]).run()
    app.button(key="reset_filters").click().run()
    assert not app.exception
    assert app.radio(key="filter_margin_preset").value == "0%"
    for key in ("filter_comps", "filter_markets", "filter_bookmakers", "filter_statuses"):
        assert app.multiselect(key=key).value == []
    assert app.selectbox(key="filter_kickoff").value == "Allir leikir"
    assert app.selectbox(key="filter_odds_age").value == "Engin mörk"
    assert kpis(app)["Arbitrage-tækifæri"] == "9"
    assert opportunity_count(app) == 9


def test_empty_results_message(app):
    set_margin(app, 50.0)
    assert not app.exception
    assert opportunity_count(app) == 0
    assert kpis(app)["Arbitrage-tækifæri"] == "0" and kpis(app)["Hæsta framlegð"] == "–"
    assert any("Engin tækifæri passa við síurnar" in i.value for i in dash(app).info)
    assert not [s for s in app.selectbox if s.key == "detail"]
    assert any("Engin tækifæri með 5%" in i.value for i in high(app).info)


# ---------------------------------------------------------------------------
# Valið tækifæri fylgir síunum
# ---------------------------------------------------------------------------

def visible_labels(at: AppTest) -> list[str]:
    df = opp_table(at)
    return [f"{a} · {b} · {c}" for a, b, c in zip(df["Leikur"], df["Markaður"], df["Framlegð"])]


def selected_label(at: AppTest) -> str:
    sb = at.selectbox(key="detail")
    return sb.options[sb.index]


def panel_heading(at: AppTest) -> str:
    return next(m.value for m in dash(at).markdown if m.value.startswith("#### "))


def assert_selection_visible(at: AppTest) -> None:
    labels = visible_labels(at)
    assert at.selectbox(key="detail").options == labels
    assert selected_label(at) in labels
    leikur, markadur, _ = selected_label(at).split(" · ")
    assert panel_heading(at) == f"#### {leikur} · {markadur}"


def select(at: AppTest, startswith: str) -> None:
    sb = at.selectbox(key="detail")
    sb.select_index(next(i for i, o in enumerate(sb.options) if o.startswith(startswith))).run()


def test_selection_kept_when_still_visible(app):
    select(app, "Bayern München – Borussia Dortmund")
    app.multiselect(key="filter_comps").set_value(["Bundesliga"]).run()
    assert_selection_visible(app)
    assert selected_label(app).startswith("Bayern München")


def test_selection_reset_when_filtered_out(app):
    select(app, "Real Madrid – Atlético Madrid · BTTS")
    app.multiselect(key="filter_markets").set_value(["Over/Under 1.5"]).run()
    assert_selection_visible(app)
    assert selected_label(app) == visible_labels(app)[0]


def test_selection_follows_status_and_margin(app):
    select(app, "Napoli – AS Roma")  # Úrelt (stuðlar ekki samtímis)
    app.multiselect(key="filter_statuses").set_value(["Uppfyllir skráð skilyrði"]).run()
    assert_selection_visible(app)
    assert not selected_label(app).startswith("Napoli")
    app.radio(key="filter_margin_preset").set_value("5%").run()
    assert_selection_visible(app)


def test_details_show_risk_reasons(app):
    select(app, "Napoli – AS Roma")
    texts = [m.value for m in dash(app).markdown]
    assert any("Ástæður stöðu" in t for t in texts)
    assert any("ekki samtímis" in t for t in texts)
    assert any("🔴 Úrelt" in e.value for e in dash(app).error)
    assert any("Ekkert tækifæri er öruggt eða áhættulaust" in c.value for c in dash(app).caption)


# ---------------------------------------------------------------------------
# 5%+ flipinn
# ---------------------------------------------------------------------------

def test_high_margin_tab(app):
    tab = high(app)
    headings = [m.value for m in tab.markdown if m.value.startswith("#### ")]
    assert headings == ["#### 🧪 DEMO · Eintracht Frankfurt – VfB Stuttgart · Over/Under 3.5",
                        "#### 🧪 DEMO · Bayern München – Borussia Dortmund · Over/Under 1.5"]
    assert any("ekki** raunveruleg veðmálatækifæri" in w.value for w in tab.warning)
    labels = [m.label for m in tab.metric]
    for label in ("Fræðileg framlegð", "Raunframlegð", "Lægsti nettóhagnaður", "Aldur stuðla"):
        assert labels.count(label) == 2
    assert int(kpis(app)["Fjöldi ≥ 5%"]) == len(headings)


def test_high_margin_tab_follows_filters(app):
    app.multiselect(key="filter_markets").set_value(["Over/Under 1.5"]).run()
    headings = [m.value for m in high(app).markdown if m.value.startswith("#### ")]
    assert headings == ["#### 🧪 DEMO · Bayern München – Borussia Dortmund · Over/Under 1.5"]


# ---------------------------------------------------------------------------
# Viðvaranir
# ---------------------------------------------------------------------------

def _count(url: str, model) -> int:
    from sqlalchemy import func, select

    from scanner.db import Database

    db = Database(url)
    try:
        with db.session() as s:
            return s.scalar(select(func.count()).select_from(model))
    finally:
        db.dispose()


def unread_alerts(at: AppTest) -> list[str]:
    return [w.value for w in at.main.warning if w.icon == "🆕"]


def alert_toasts(at: AppTest) -> list[str]:
    return [t.value for t in at.toast if t.icon == "🔔"]


def test_alerts_shown_and_demo_labelled(app):
    boxes = unread_alerts(app)
    assert len(boxes) == 2
    assert all(v.startswith("🧪 DEMO · ") for v in boxes)
    for part in ("Bayern München", "Over/Under 1.5", "Coolbet", "Epicbet", "6,00%", "lægsti nettóhagnaður",
                 "aldur stuðla"):
        assert any(part in v for v in boxes)
    toasts = alert_toasts(app)
    assert len(toasts) == 2 and all(t.startswith("🧪 DEMO · ") for t in toasts)
    assert any(e.label.startswith("🔔 Viðvaranir (2 ólesnar)") for e in app.expander)


def test_alerts_not_repeated(app, temp_database_url):
    from scanner.db import AlertRow

    assert _count(temp_database_url, AlertRow) == 2
    app.run()
    assert alert_toasts(app) == []  # endurhleðsla: engin ný viðvörun
    app.button(key="refresh_data").click().run()
    assert alert_toasts(app) == []  # uppfærsla: engin ný viðvörun
    restarted = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert alert_toasts(restarted) == []  # endurræsing: engin ný viðvörun
    assert len(unread_alerts(restarted)) == 2  # ...en þær ólesnu sjást enn
    assert _count(temp_database_url, AlertRow) == 2


def test_alert_threshold_change(app, temp_database_url):
    from scanner.db import AlertRow

    app.number_input(key="alert_threshold").set_value(3.0).run()
    assert not app.exception
    n3 = _count(temp_database_url, AlertRow)
    assert n3 > 2
    app.number_input(key="alert_threshold").set_value(5.0).run()
    app.number_input(key="alert_threshold").set_value(3.0).run()
    assert _count(temp_database_url, AlertRow) == n3


def test_mark_alerts_read(app):
    app.button(key="alerts_mark_read").click().run()
    assert not app.exception
    assert unread_alerts(app) == []
    assert len([i for i in app.main.info if i.icon == "✔️"]) == 2  # sjást áfram sem lesnar


# ---------------------------------------------------------------------------
# Uppfærsla gagna og DEMO/LIVE
# ---------------------------------------------------------------------------

def _db_counts(url: str) -> tuple[int, int, int]:
    from scanner.db import OddsSnapshotRow, OpportunityObservationRow, OpportunityRow

    return tuple(_count(url, m) for m in (OpportunityRow, OddsSnapshotRow, OpportunityObservationRow))


def test_refresh_does_not_create_new_data(app, temp_database_url):
    before_db, before_kpi = _db_counts(temp_database_url), kpis(app)
    before_table = opp_table(detailed(app))
    for _ in range(3):
        app.button(key="refresh_data").click().run()
        assert not app.exception
    assert _db_counts(temp_database_url) == before_db
    after = kpis(app)
    assert {k: v for k, v in after.items() if k != "Síðasta gagnauppfærsla"} == \
           {k: v for k, v in before_kpi.items() if k != "Síðasta gagnauppfærsla"}
    assert opp_table(app)["Stuðlar"].tolist() == before_table["Stuðlar"].tolist()


def test_live_mode_is_empty_and_separate(app, temp_database_url):
    from scanner.db import AlertRow

    app.radio(key="data_mode").set_value("LIVE").run()
    assert not app.exception
    k = kpis(app)
    assert (k["Fjöldi leikja"], k["Arbitrage-tækifæri"], k["Hæsta framlegð"]) == ("0", "0", "–")
    assert any("Engin LIVE-gögn" in i.value for i in dash(app).info)
    assert any("Engin LIVE-tenging" in c.value for c in app.sidebar.caption)
    assert any("Engar LIVE-viðvaranir" in i.value for i in app.main.info)
    assert any("Engin LIVE-tækifæri" in i.value for i in hist(app).info)
    assert not app.get("plotly_chart")
    assert _count(temp_database_url, AlertRow) == 2  # DEMO-viðvaranir eru til, en ekki sýndar í LIVE


def test_no_secrets_displayed(monkeypatch, temp_database_url):
    monkeypatch.setenv("COOLBET_API_KEY", "LEYNI-coolbet-123")
    monkeypatch.setenv("BET365_API_KEY", "LEYNI-bet365-456")
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception
    dump = repr(at._tree)
    assert "LEYNI" not in dump and temp_database_url not in dump


# ---------------------------------------------------------------------------
# Veðmálamörk (áfangi 4)
# ---------------------------------------------------------------------------

def test_manual_limits_override(app):
    before = len(opp_table(app)[opp_table(app)["Staða"] == "🟢 Uppfyllir skráð skilyrði"])
    app.checkbox(key="manual_epicbet").check().run()
    app.number_input(key="lim_max_epicbet").set_value(10_000).run()
    after = len(opp_table(app)[opp_table(app)["Staða"] == "🟢 Uppfyllir skráð skilyrði"])
    assert after < before
    table = limits_tab(app).table[0].value
    epic = table[table["Valkostur"] == "Epicbet"].iloc[0]
    assert epic["Uppruni"] == "Handvirkt" and epic["Hámarksveðmál"] == "10.000 kr."
    assert set(opp_table(detailed(app))["Uppspretta"]) == {"DEMO"}  # stuðlar eru enn DEMO


# ---------------------------------------------------------------------------
# Reiknivél (áfangar 2 og 4)
# ---------------------------------------------------------------------------

def fill_known_limits(at: AppTest, side: str, max_stake: float = 1_000_000) -> None:
    at.number_input(key=f"max_{side}").set_value(max_stake)
    at.number_input(key=f"bal_{side}").set_value(1_000_000)
    at.number_input(key=f"min_{side}").set_value(100)
    at.number_input(key=f"step_{side}").set_value(1)
    at.number_input(key=f"comm_{side}").set_value(0)
    at.number_input(key=f"fee_{side}").set_value(0)
    at.checkbox(key=f"confirmed_{side}").check()


def test_calculator_defaults(app):
    assert app.number_input(key="total").value == 100_000
    m = metrics(calc(app))
    assert (m["Arbitrage"], m["Fræðileg framlegð"], m["Lægsti nettóhagnaður"]) == ("Já", "3,73%", "3.734,10 kr.")
    assert any("Óstaðfest" in w.value for w in calc(app).warning)


def test_calculator_changes_total(app):
    app.number_input(key="total").set_value(200_000).run()
    assert metrics(calc(app))["Lægsti nettóhagnaður"].startswith("7.46")


def test_calculator_no_arbitrage(app):
    app.text_input(key="odds_A").set_value("1,90")
    app.text_input(key="odds_B").set_value("1,90").run()
    m = metrics(calc(app))
    assert (m["Arbitrage"], m["Lægsti nettóhagnaður"]) == ("Nei", "-5.000,00 kr.")


def test_calculator_invalid_odds(app):
    app.text_input(key="odds_A").set_value("abc").run()
    assert not app.exception
    assert any("Ógildur innsláttur" in e.value for e in calc(app).error)


def test_calculator_rejects_100k_with_30k_max(app):
    fill_known_limits(app, "A", max_stake=30_000)
    fill_known_limits(app, "B")
    app.run()
    tab = calc(app)
    assert any("Uppfyllir ekki fjárhæðarskilyrði" in e.value for e in tab.error)
    assert not tab.success
    assert any("49.398 kr. er yfir hámarksveðmáli 30.000 kr." in m.value for m in tab.markdown)
    assert metrics(tab)["Hæsta mögulega fjárhæð"] == "60.733 kr."
    app.number_input(key="max_B").set_value(25_000).run()
    assert metrics(calc(app))["Hæsta mögulega fjárhæð"] == "49.406 kr."
    app.number_input(key="total").set_value(49_406).run()
    assert any("Uppfyllir skráð fjárhæðarskilyrði" in s.value for s in calc(app).success)


def test_calculator_max_stake_40k(app):
    fill_known_limits(app, "A", max_stake=40_000)
    fill_known_limits(app, "B")
    app.run()
    assert metrics(calc(app))["Hæsta mögulega fjárhæð"] == "80.977 kr."


def test_calculator_all_known_meets(app):
    fill_known_limits(app, "A")
    fill_known_limits(app, "B")
    app.run()
    tab = calc(app)
    assert any("Uppfyllir skráð fjárhæðarskilyrði" in s.value for s in tab.success)
    assert any("Handvirkar upplýsingar" in s.value for s in tab.success)
    assert any("ekki trygging" in c.value for c in tab.caption)
    app.number_input(key="max_A").set_value(None).run()
    assert not calc(app).success


def test_calculator_eur_account(app):
    fill_known_limits(app, "A")
    fill_known_limits(app, "B")
    app.selectbox(key="cur_A").set_value("EUR")
    app.number_input(key="step_A").set_value(0.01)
    app.run()
    assert not app.exception
    assert any("EUR" in str(t.value) for t in calc(app).table)


# ---------------------------------------------------------------------------
# Saga og greining (áfangi 5)
# ---------------------------------------------------------------------------

def test_history_tab(app):
    tab = hist(app)
    assert any("DEMO-saga" in w.value for w in tab.warning)
    m = metrics(tab)
    assert m["Einstök tækifæri"] == "9" and m["Yfir 5%"] == "2"
    assert "Uppfylla 100.000 kr. skilyrði" in m
    assert len(app.get("plotly_chart")) == 2
    assert len(app.get("download_button")) == 1


def test_app_uses_temporary_database(app, temp_database_url):
    from scanner.config import load_settings
    from scanner.db import DEFAULT_DATABASE_URL

    assert load_settings().database_url == temp_database_url != DEFAULT_DATABASE_URL
    assert _db_counts(temp_database_url)[0] == 9


def test_reruns_and_restarts_do_not_create_new_opportunities(app, temp_database_url):
    before = _db_counts(temp_database_url)
    app.radio(key="filter_margin_preset").set_value("3%").run()
    app.number_input(key="scan_total").set_value(50_000).run()  # sagan miðast samt við 100.000 kr.
    AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert _db_counts(temp_database_url) == before


def test_history_period_switch(app):
    for period in ("Síðustu 7 dagar", "Síðustu 30 dagar", "Síðustu 24 klst."):
        app.radio(key="history_period").set_value(period).run()
        assert not app.exception
        assert metrics(hist(app))["Einstök tækifæri"] == "9"


def test_opportunity_keys_unique():
    from datetime import UTC, datetime

    from scanner.demo import run_demo_scan

    report = run_demo_scan(datetime(2026, 11, 1, 12, tzinfo=UTC))
    keys = [o.key for o in report.opportunities]
    assert len(keys) == len(set(keys))
