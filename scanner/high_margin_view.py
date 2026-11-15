"""Flipinn „5%+ tækifæri“: aðeins tækifæri með a.m.k. 5% fræðilega framlegð."""

from __future__ import annotations

import streamlit as st

from scanner.dashboard import HIGH_MARGIN_PCT, Row, profit_note
from scanner.db import DataMode
from scanner.formatting import format_isk, format_number, format_percent
from scanner.opportunities_view import render_risk_badge
from scanner.risk import RISK_DISCLAIMER, format_age

DEMO_WARNING = (
    "🧪 **DEMO** – þetta eru tilbúin sýnigögn, **ekki** raunveruleg veðmálatækifæri. "
    "Ekki leggja undir á grundvelli þeirra."
)


def render_high_margin(rows: list[Row], mode: DataMode) -> None:
    st.subheader(f"Tækifæri með að minnsta kosti {format_percent(HIGH_MARGIN_PCT, 0)} framlegð")
    st.caption("Aðrar síur í hliðarstikunni gilda líka. Raðað eftir framlegð, hæst fyrst.")
    if mode is DataMode.DEMO:
        st.warning(DEMO_WARNING)

    if not rows:
        st.info(f"Engin tækifæri með {format_percent(HIGH_MARGIN_PCT, 0)} framlegð eða meira passa við síurnar.")
        return

    st.markdown(f"**{len(rows)}** tækifæri")
    for r in rows:
        o, f = r.opportunity, r.opportunity.feasibility
        with st.container(border=True):
            title = f"{o.match.label} · {o.market.label}"
            st.markdown(f"#### {'🧪 DEMO · ' if o.is_demo else ''}{title}")
            st.caption(f"{o.match.competition_name} · upphaf {o.match.kickoff_utc:%d.%m.%Y %H:%M} UTC")
            render_risk_badge(r)  # staðan sést ÁÐUR en hagnaðartölur
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Fræðileg framlegð", format_percent(f.theoretical_margin_pct))
            c2.metric("Raunframlegð", "–" if f.real_margin_pct is None else format_percent(f.real_margin_pct),
                      help="Eftir námundun, gjöld, gjaldmiðla og aðrar þekktar forsendur")
            c3.metric("Lægsti nettóhagnaður", "–" if f.worst_case_profit is None else format_isk(f.worst_case_profit, 2))
            c4.metric("Aldur stuðla", format_age(r.risk.odds_age))
            if note := profit_note(r):
                st.caption(f"⚠️ Hagnaðartölur eru {note} ({r.risk.status.value}).")
            st.table([
                {
                    "Valkostur": f"{q.outcome} hjá {bm.name}",
                    "Stuðull": format_number(q.price),
                    "Fjárhæð": side.stake_display if side else "–",
                }
                for q, bm, side in ((o.quote_a, o.bookmaker_a, f.side_a), (o.quote_b, o.bookmaker_b, f.side_b))
            ])
            st.markdown(f"Heildarfjárhæð: **{format_isk(f.requested_total)}**")
            if r.risk.reasons:
                with st.expander("Ástæður stöðu"):
                    for reason in r.risk.reasons:
                        st.markdown(f"- {reason}")
    st.caption(RISK_DISCLAIMER)
