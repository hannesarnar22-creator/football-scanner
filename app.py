"""Football Arbitrage Scanner – Streamlit mælaborð.

DEMO-hamur: tilbúin gögn. Engin API-tenging og engin sjálfvirk veðmál.
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from scanner.alerts import generate_alerts
from scanner.alerts_view import render_alerts_panel, show_new_alert_toasts
from scanner.calculator_view import render_calculator
from scanner.config import load_settings
from scanner.dashboard import apply_filters, build_rows, compute_kpis, high_margin_rows, profit_source_text
from scanner.db import Database, DataMode
from scanner.demo import run_demo_scan
from scanner.demo_data import default_anchor, demo_bookmakers
from scanner.formatting import format_isk, format_percent, format_timestamp
from scanner.high_margin_view import render_high_margin
from scanner.history import ingest_scan
from scanner.history_view import render_history
from scanner.opportunities_view import render_data_quality, render_limits_editor, render_opportunity_table
from scanner.scan import ScanReport
from scanner.sidebar import render_sidebar

st.set_page_config(page_title="Football Arbitrage Scanner", page_icon="⚽", layout="wide")

st.markdown(
    """
    <style>
    :root { --ok: #22c55e; --warn: #facc15; --bad: #f87171; }
    .demo-badge {
        display: inline-block; padding: 4px 12px; border-radius: 999px;
        background: #f59e0b22; color: #fbbf24; border: 1px solid #f59e0b;
        font-weight: 600; font-size: 0.85rem; letter-spacing: 0.05em;
    }
    div[data-testid="stMetric"] {
        background: #131c2e; border: 1px solid #1f2a44;
        border-radius: 12px; padding: 14px 18px;
    }
    div[data-testid="stMetricValue"] { color: #f8fafc; font-size: 1.9rem; }
    /* Langar tölur og heiti fara í næstu línu í stað þess að klippast með „…“ */
    div[data-testid="stMetricValue"] *, div[data-testid="stMetricLabel"] * {
        white-space: normal !important; overflow: visible !important;
        text-overflow: clip !important; overflow-wrap: anywhere;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

settings = load_settings()
ss = st.session_state


@st.cache_resource
def get_database(url: str) -> Database:
    return Database(url)


db = get_database(settings.database_url)

# DEMO-gagnasettið er fest við akkeri: uppfærsla breytir tíma, ekki leikjum eða stuðlum
if "demo_anchor" not in ss:
    ss["demo_anchor"] = default_anchor(datetime.now(UTC))
if "data_refreshed_at" not in ss:
    ss["data_refreshed_at"] = datetime.now(UTC)

side = render_sidebar(settings, ss["data_refreshed_at"])
now = ss["data_refreshed_at"]
mode = side.mode

# --- Haus ---
st.title("⚽ Football Arbitrage Scanner")
st.caption("Pre-match arbitrage milli Coolbet, bet365 og Epicbet · Over/Under 1.5/2.5/3.5 og BTTS")
if mode is DataMode.DEMO:
    st.markdown('<span class="demo-badge">DEMO-HAMUR</span>', unsafe_allow_html=True)
    st.info(
        "Forritið keyrir í DEMO-ham. Allir leikir, stuðlar og veðmálamörk eru tilbúin sýnigögn – "
        "engin tenging er við veðmálasíður og engin veðmál eru sett."
    )
if ss.pop("refresh_notice", False):
    st.toast("Gögn uppfærð. DEMO-gagnasettið er óbreytt – engin ný tilbúin tækifæri.", icon="🔄")

kpi_box = st.container()
alerts_box = st.container()
tab_dash, tab_high, tab_calc, tab_hist, tab_limits = st.tabs(
    ["📊 Mælaborð", "🔥 5%+ tækifæri", "🧮 Reiknivél", "📈 Saga og greining", "⚙️ Veðmálamörk"]
)

# --- Veðmálamörk (teiknuð fyrst því leitin notar þau) ---
with tab_limits:
    if mode is DataMode.DEMO:
        bookmakers = render_limits_editor(demo_bookmakers(now), now)
    else:
        bookmakers = {}
        st.info("Engar LIVE-veðmálasíður eru tengdar. Veðmálamörk eru skráð þegar API-tengingar koma til.")


def report_signature(total: int) -> tuple:
    """Allt sem hefur áhrif á niðurstöðu leitar. Síur eru EKKI hluti af henni."""
    return (
        mode.value, now.isoformat(), ss["demo_anchor"].isoformat(), total,
        tuple(sorted((k, b.limits.model_dump_json()) for k, b in bookmakers.items())),
        tuple(sorted((k, str(v)) for k, v in side.fx_rates.items())),
    )


def get_report(total: int) -> ScanReport:
    """Leit, geymd í session_state svo síubreytingar endurreikni ekki allt."""
    if mode is DataMode.LIVE:
        return ScanReport()  # engin LIVE-uppspretta til
    sig = report_signature(total)
    cache = ss.setdefault("report_cache", {})
    if sig not in cache:
        if len(cache) > 6:
            cache.clear()
        cache[sig] = run_demo_scan(now, total, bookmakers, side.fx_rates, anchor=ss["demo_anchor"])
    return cache[sig]


report = get_report(side.total_stake)

# --- Saga og viðvaranir (alltaf miðað við sjálfgefna 100.000 kr. svo sagan sé sambærileg) ---
if mode is DataMode.DEMO:
    ref_report = report if side.total_stake == settings.total_stake_isk else get_report(settings.total_stake_isk)
    hist_sig = report_signature(settings.total_stake_isk)
    if ss.get("history_signature") != hist_sig:
        ingest_scan(db, ref_report, bookmakers, DataMode.DEMO, now)
        ss["history_signature"] = hist_sig
    alert_sig = (hist_sig, str(side.alert_threshold))
    if ss.get("alerts_signature") != alert_sig:
        show_new_alert_toasts(generate_alerts(db, ref_report, DataMode.DEMO, now, side.alert_threshold))
        ss["alerts_signature"] = alert_sig

rows_sig = report_signature(side.total_stake)
if ss.get("rows_signature") != rows_sig:
    ss["rows"] = build_rows(report, now)
    ss["rows_signature"] = rows_sig
rows = ss["rows"]
filtered = apply_filters(rows, side.filters, now)
kpis = compute_kpis(report, filtered, side.filters, now, now if report.matches else None)

# --- Yfirlitstölur ---
with kpi_box:
    # Tvær raðir með þremur reitum svo langar tölur (krónur, dagsetning) komist fyrir
    c = st.columns(3) + st.columns(3)
    c[0].metric("Fjöldi leikja", kpis.match_count)
    c[1].metric("Arbitrage-tækifæri", kpis.arbitrage_count)
    c[2].metric("Fjöldi ≥ 5%", kpis.high_margin_count)
    c[3].metric("Hæsta framlegð", "–" if kpis.best_margin_pct is None else format_percent(kpis.best_margin_pct))
    c[4].metric("Hæsti áætlaði nettóhagnaður", "–" if kpis.best_profit is None else format_isk(kpis.best_profit, 2),
                help="Hæsti LÆGSTI nettóhagnaður (versta niðurstaða) meðal tækifæra sem passa við síurnar")
    c[5].metric("Síðasta gagnauppfærsla", "–" if kpis.last_update is None else format_timestamp(kpis.last_update) + " UTC")
    st.caption(f"Tölurnar fylgja síunum í hliðarstikunni · gagnahamur **{mode.value}** · "
               f"heildarfjárhæð {format_isk(side.total_stake)}")
    if kpis.best_profit_row is not None:
        st.caption(profit_source_text(kpis.best_profit_row))

with alerts_box:
    render_alerts_panel(db, mode, format_percent(side.alert_threshold, 1))

no_data_message = (
    "Engin LIVE-gögn: engin API-tenging hefur verið sett upp. Veldu DEMO í hliðarstikunni til að sjá sýnigögn."
    if mode is DataMode.LIVE else
    "Engin tækifæri passa við síurnar. Prófaðu að lækka lágmarksframlegð eða smelltu á „Endurstilla síur“."
)

with tab_dash:
    if mode is DataMode.LIVE:
        st.info(no_data_message)
    else:
        render_opportunity_table(filtered, no_data_message, now)
        render_data_quality(report)

with tab_high:
    if mode is DataMode.LIVE:
        st.info(no_data_message)
    else:
        render_high_margin(high_margin_rows(rows, side.filters, now), mode)

with tab_calc:
    render_calculator(settings, side.fx_rates)

with tab_hist:
    render_history(db, now, mode)
