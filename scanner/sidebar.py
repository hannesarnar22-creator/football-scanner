"""Hliðarstika: gagnahamur, uppfærsla, viðvörunarmörk, síur og gengi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import streamlit as st

from scanner.arbitrage import to_decimal
from scanner.config import BOOKMAKERS, MARKETS, Settings
from scanner.dashboard import KICKOFF_WINDOWS, MARGIN_PRESETS, ODDS_AGE_LIMITS, DashboardFilters
from scanner.db import DataMode
from scanner.demo_data import DEMO_FX_RATES
from scanner.formatting import format_timestamp
from scanner.risk import RiskStatus
from scanner.teams import default_registry

ALERT_DEFAULT = 5.0

# Lykill -> sjálfgefið gildi. Notað bæði við fyrstu teikningu og endurstillingu.
FILTER_DEFAULTS: dict[str, object] = {
    "filter_margin_preset": "0%",
    "filter_min_margin": 0.0,
    "filter_comps": [],
    "filter_markets": [],
    "filter_bookmakers": [],
    "filter_kickoff": "Allir leikir",
    "filter_odds_age": "Engin mörk",
    "filter_statuses": [],
}


def refresh_data() -> None:
    """Callback: nýr uppfærslutími. Í DEMO helst gagnasettið (akkeri) óbreytt –
    engin ný tilbúin tækifæri og engar handahófskenndar stuðlabreytingar."""
    st.session_state["data_refreshed_at"] = datetime.now(UTC)
    st.session_state["refresh_notice"] = True


def reset_filters() -> None:
    """Callback: keyrt ÁÐUR en widget-in eru teiknuð í næstu keyrslu."""
    for key, value in FILTER_DEFAULTS.items():
        st.session_state[key] = list(value) if isinstance(value, list) else value
    st.session_state.pop("detail", None)


def _init_defaults() -> None:
    for key, value in FILTER_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = list(value) if isinstance(value, list) else value


@dataclass(frozen=True)
class SidebarState:
    mode: DataMode
    total_stake: int
    alert_threshold: Decimal
    fx_rates: dict[str, Decimal]
    filters: DashboardFilters
    refresh_clicked: bool


def filters_from_state() -> DashboardFilters:
    preset = MARGIN_PRESETS[st.session_state["filter_margin_preset"]]
    min_margin = preset if preset is not None else to_decimal(st.session_state["filter_min_margin"])
    return DashboardFilters(
        min_margin_pct=min_margin,
        competitions=frozenset(st.session_state["filter_comps"]),
        markets=frozenset(st.session_state["filter_markets"]),
        bookmakers=frozenset(st.session_state["filter_bookmakers"]),
        kickoff_within=KICKOFF_WINDOWS[st.session_state["filter_kickoff"]],
        max_odds_age=ODDS_AGE_LIMITS[st.session_state["filter_odds_age"]],
        statuses=frozenset(RiskStatus(s) for s in st.session_state["filter_statuses"]),
    )


def render_sidebar(settings: Settings, refreshed_at: datetime | None) -> SidebarState:
    _init_defaults()
    with st.sidebar:
        st.header("⚽ Stjórnborð")

        mode = DataMode(st.radio(
            "Gagnahamur", [m.value for m in DataMode],
            index=0 if settings.is_demo else 1, horizontal=True, key="data_mode",
        ))
        refresh = st.button("🔄 Uppfæra gögn", key="refresh_data", on_click=refresh_data, width="stretch")
        refreshed_at = st.session_state.get("data_refreshed_at", refreshed_at)
        if mode is DataMode.DEMO:
            st.caption("Gagnauppspretta: 🧪 **DEMO-gagnasett** (tilbúin gögn) · engin API-tenging")
        else:
            st.caption("Gagnauppspretta: ⚪ **Engin LIVE-tenging stillt** · engin API-tenging er til")
        st.caption(f"Síðast uppfært: **{format_timestamp(refreshed_at) + ' UTC' if refreshed_at else '–'}**")

        total = st.number_input(
            "Heildarfjárhæð (kr.)", min_value=2, value=settings.total_stake_isk, step=1000, key="scan_total"
        )
        alert_threshold = st.number_input(
            "Viðvörunarmörk (% framlegð)", min_value=0.0, max_value=50.0, value=ALERT_DEFAULT, step=0.5,
            key="alert_threshold",
        )

        st.subheader("Síur")
        st.radio("Lágmarksframlegð", list(MARGIN_PRESETS), horizontal=True, key="filter_margin_preset")
        if st.session_state["filter_margin_preset"] == "Sérsniðið":
            st.number_input("Sérsniðin lágmarksframlegð (%)", min_value=0.0, max_value=50.0, step=0.25,
                            key="filter_min_margin")
        competitions = [c.name for c in default_registry().competitions.values()]
        st.multiselect("Keppni", competitions, key="filter_comps", placeholder="Allar keppnir")
        st.multiselect("Markaður", MARKETS, key="filter_markets", placeholder="Allir markaðir")
        st.multiselect("Veðmálasíður", list(BOOKMAKERS), key="filter_bookmakers", placeholder="Allar síður",
                       help="Aðeins tækifæri þar sem BÁÐAR síðurnar eru valdar")
        st.selectbox("Upphafstími leiks", list(KICKOFF_WINDOWS), key="filter_kickoff")
        st.selectbox("Hámarksaldur stuðla", list(ODDS_AGE_LIMITS), key="filter_odds_age")
        st.multiselect("Staða", [s.value for s in RiskStatus], key="filter_statuses", placeholder="Allar stöður")
        st.button("↺ Endurstilla síur", key="reset_filters", on_click=reset_filters, width="stretch")

        with st.expander("Gengi"):
            fx_rates = {
                cur: to_decimal(st.number_input(
                    f"1 {cur} = kr.", min_value=0.01, value=float(rate), step=0.5, format="%.2f", key=f"fx_{cur}"
                ))
                for cur, rate in DEMO_FX_RATES.items()
            }
            st.caption("Sjálfgefið er DEMO-gengi. Gengisáhætta er ekki metin.")

        st.caption("Engin veðmál eru sett sjálfkrafa. Allir markaðir: 90 mín. + uppbótartími, pre-match.")

    return SidebarState(
        mode=mode,
        total_stake=int(total),
        alert_threshold=to_decimal(alert_threshold),
        fx_rates=fx_rates,
        filters=filters_from_state(),
        refresh_clicked=refresh,
    )
