"""Viðvaranir í Streamlit. Engin tölvupóstur, SMS eða push-tilkynning."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from scanner.alerts import Alert, list_alerts, mark_all_read
from scanner.db import Database, DataMode
from scanner.formatting import format_isk, format_number, format_percent
from scanner.risk import format_age


def alert_text(a: Alert) -> str:
    prefix = "🧪 DEMO · " if a.is_demo else ""
    profit = "–" if a.worst_case_profit is None else format_isk(a.worst_case_profit, 2)
    return (
        f"{prefix}**{a.match_label}** · {a.market_label} · **{format_percent(a.margin_pct)}**  \n"
        f"{a.bookmaker_a} ({a.outcome_a}) {format_number(a.odds_a)} / "
        f"{a.bookmaker_b} ({a.outcome_b}) {format_number(a.odds_b)} · "
        f"lægsti nettóhagnaður {profit} af {format_isk(a.total_stake)} · {a.status} · "
        f"aldur stuðla {format_age(timedelta(seconds=a.odds_age_seconds))}"
    )


def show_new_alert_toasts(new_alerts: list[Alert]) -> None:
    """Sýnir aðeins viðvaranir sem urðu til í ÞESSARI keyrslu."""
    for a in new_alerts[:5]:
        st.toast(f"{'🧪 DEMO · ' if a.is_demo else ''}Nýtt tækifæri {format_percent(a.margin_pct)}: "
                 f"{a.match_label} · {a.market_label}", icon="🔔")
    if len(new_alerts) > 5:
        st.toast(f"…og {len(new_alerts) - 5} fleiri nýjar viðvaranir", icon="🔔")


def render_alerts_panel(db: Database, mode: DataMode, threshold_text: str) -> None:
    unread = list_alerts(db, mode, only_unread=True)
    recent = list_alerts(db, mode, limit=20)
    label = f"🔔 Viðvaranir ({len(unread)} ólesnar) · mörk {threshold_text}"
    with st.expander(label, expanded=bool(unread)):
        if mode is DataMode.DEMO:
            st.caption("🧪 DEMO-viðvaranir – byggðar á tilbúnum gögnum, ekki raunverulegum tækifærum.")
        if not recent:
            st.info(f"Engar {mode.value}-viðvaranir ennþá.")
            return
        for a in recent:
            if a.is_read:
                st.info(alert_text(a), icon="✔️")
            else:
                st.warning(alert_text(a), icon="🆕")
        if unread and st.button("Merkja allar lesnar", key="alerts_mark_read"):
            mark_all_read(db, mode)
            st.rerun()
        st.caption("Hvert tækifæri fær í mesta lagi eina viðvörun. Engar tilkynningar eru sendar út fyrir forritið.")
