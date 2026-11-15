"""Streamlit-hlutar: veðmálamörk, arbitrage-tafla og upplýsingaspjald."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd
import streamlit as st

from scanner.arbitrage import to_decimal
from scanner.dashboard import Row, profit_note
from scanner.formatting import format_isk, format_number, format_percent
from scanner.models import Bookmaker, DataOrigin, StakeLimits
from scanner.risk import RISK_DISCLAIMER, RiskStatus, format_age
from scanner.scan import ScanReport
from scanner.ui_common import limits_rows, max_feasible_text, side_rows

CURRENCIES = ["ISK", "EUR", "USD"]
DETAIL_KEY = "detail"
VIEW_COMPACT = "Samantekt"
VIEW_DETAILED = "Ítarleg"

STATUS_CSS_COLOR = {"green": "#22c55e", "yellow": "#facc15", "red": "#f87171"}


def detail_label(r: Row) -> str:
    o = r.opportunity
    return f"{o.match.label} · {o.market.label} · {format_percent(o.margin_pct)}"


# ---------------------------------------------------------------------------
# Veðmálamörk
# ---------------------------------------------------------------------------

def _num(label: str, value: Decimal | None, key: str, step: float) -> Decimal | None:
    v = st.number_input(label, min_value=0.0, value=None if value is None else float(value), step=step, key=key)
    return None if v is None else to_decimal(v)


def render_limits_editor(base: dict[str, Bookmaker], now: datetime) -> dict[str, Bookmaker]:
    """Sýnir veðmálamörk hverrar síðu og leyfir handvirka skráningu.

    Handvirk skráning merkir uppruna 'Handvirkt' og staðfestingartíma núna.
    Tómur reitur = UNKNOWN.
    """
    result = dict(base)
    st.subheader("Veðmálamörk hjá veðmálasíðum")
    table = st.empty()  # fyllt eftir innslátt svo taflan sýni gildin sem eru notuð
    cols = st.columns(len(base))
    for col, (bm_id, bm) in zip(cols, base.items()):
        with col:
            st.markdown(f"**{bm.name}**")
            if not st.checkbox("Skrá handvirkt", key=f"manual_{bm_id}"):
                continue
            lim = bm.limits
            cur_index = CURRENCIES.index(lim.currency) if lim.currency in CURRENCIES else 0
            currency = st.selectbox("Gjaldmiðill", CURRENCIES, index=cur_index, key=f"lim_cur_{bm_id}")
            result[bm_id] = bm.model_copy(update={"limits": StakeLimits(
                source=DataOrigin.MANUAL,
                currency=currency,
                max_stake=_num("Hámarksveðmál", lim.max_stake, f"lim_max_{bm_id}", 1000.0),
                available_balance=_num("Tiltæk inneign", lim.available_balance, f"lim_bal_{bm_id}", 1000.0),
                min_stake=_num("Lágmarksveðmál", lim.min_stake, f"lim_min_{bm_id}", 10.0),
                stake_step=_num("Veðmálaskref", lim.stake_step, f"lim_step_{bm_id}", 1.0) or None,
                commission_pct=_num("Þóknun (%)", lim.commission_pct, f"lim_comm_{bm_id}", 0.5),
                fixed_fee=_num("Föst gjöld", lim.fixed_fee, f"lim_fee_{bm_id}", 10.0),
                last_confirmed_at=now,
            )})
    table.table([limits_rows(bm.name, bm.limits, now) for bm in result.values()])
    st.caption("Tómur reitur = UNKNOWN. Óþekkt mörk eru aldrei áætluð. Handvirk skráning telst staðfest núna.")
    return result


# ---------------------------------------------------------------------------
# Tafla
# ---------------------------------------------------------------------------

def table_row(r: Row, detailed: bool) -> dict[str, str]:
    o, f = r.opportunity, r.opportunity.feasibility
    profit = "–" if f.worst_case_profit is None else format_isk(f.worst_case_profit, 2)
    if profit_note(r) and f.worst_case_profit is not None:
        profit = f"≈ {profit}"  # áætlun, ekki staðfestur hagnaður
    if not detailed:
        return {
            "Leikur": o.match.label,
            "Markaður": o.market.label,
            "Framlegð": format_percent(o.margin_pct),
            "Lægsti hagnaður": profit,
            "Staða": r.risk.status.label,
            "Aldur stuðla": format_age(r.risk.odds_age),
        }
    return {
        "Leikur": o.match.label,
        "Keppni": o.match.competition_name,
        "Upphaf (UTC)": f"{o.match.kickoff_utc:%d.%m. %H:%M}",
        "Markaður": o.market.label,
        "Veðmálasíður": f"{o.bookmaker_a.name} ({o.quote_a.outcome}) / {o.bookmaker_b.name} ({o.quote_b.outcome})",
        "Stuðlar": f"{format_number(o.quote_a.price)} / {format_number(o.quote_b.price)}",
        "Framlegð": format_percent(o.margin_pct),
        "Fjárhæð A": f.side_a.stake_display if f.side_a else "–",
        "Fjárhæð B": f.side_b.stake_display if f.side_b else "–",
        "Heildarfjárhæð": format_isk(f.requested_total),
        "Lægsti hagnaður": profit,
        "Hæsta mögulega fjárhæð": max_feasible_text(f),
        "Staða": r.risk.status.label,
        "Aldur stuðla": format_age(r.risk.odds_age),
        "Uppspretta": o.data_basis.value,
    }


def _status_style(value: str) -> str:
    for s in RiskStatus:
        if value == s.label:
            return f"color: {STATUS_CSS_COLOR[s.color]}; font-weight: 600"
    return ""


def styled_table(rows: list[Row], detailed: bool):
    df = pd.DataFrame([table_row(r, detailed) for r in rows])
    return df.style.map(_status_style, subset=["Staða"])


def render_details(r: Row, now: datetime) -> None:
    o, f = r.opportunity, r.opportunity.feasibility
    st.markdown(f"#### {o.match.label} · {o.market.label}")
    st.caption(f"{o.match.competition_name} · upphaf {o.match.kickoff_utc:%d.%m.%Y %H:%M} UTC · "
               f"uppspretta: {o.data_basis.value}")
    render_risk_badge(r)
    names = (f"{o.quote_a.outcome} hjá {o.bookmaker_a.name}", f"{o.quote_b.outcome} hjá {o.bookmaker_b.name}")
    st.table(side_rows(f, names))
    st.markdown(
        f"Fræðileg framlegð **{format_percent(f.theoretical_margin_pct)}** · "
        f"raunframlegð eftir námundun og gjöld **{'–' if f.real_margin_pct is None else format_percent(f.real_margin_pct)}** · "
        f"hæsta mögulega fjárhæð **{max_feasible_text(f)}** · aldur stuðla **{format_age(r.risk.odds_age)}**"
    )
    if r.risk.reasons:
        st.markdown("**Ástæður stöðu**")
        for reason in r.risk.reasons:
            st.markdown(f"- {reason}")
    with st.expander("Veðmálamörk sem notuð voru"):
        st.table([
            limits_rows(names[0], o.bookmaker_a.limits, now),
            limits_rows(names[1], o.bookmaker_b.limits, now),
        ])
    st.caption(RISK_DISCLAIMER)


def render_risk_badge(r: Row) -> None:
    text = f"**{r.risk.status.label}**"
    match r.risk.status.color:
        case "green":
            st.success(text)
        case "yellow":
            st.warning(text)
        case _:
            st.error(text)


def render_opportunity_table(rows: list[Row], empty_message: str, now: datetime) -> None:
    st.markdown(f"**{len(rows)}** tækifæri passa við síurnar · raðað eftir framlegð (hæst fyrst)")
    if not rows:
        st.info(empty_message)
        st.session_state.pop(DETAIL_KEY, None)
        return

    view = st.radio("Sýn", [VIEW_COMPACT, VIEW_DETAILED], horizontal=True, key="table_view",
                    help="Samantekt hentar litlum skjám; ítarleg sýn sýnir alla dálka")
    st.dataframe(styled_table(rows, view == VIEW_DETAILED), hide_index=True, width="stretch", key="opp_table")
    st.caption(" · ".join(s.label for s in RiskStatus) + " — sjá ástæður í upplýsingaspjaldi. "
               "≈ = áætlaður hagnaður, ekki staðfestur (allar stöður nema 🟢).")

    visible = {r.key: r for r in rows}
    # Ef fyrra val hvarf við síun: velja fyrsta sýnilega tækifærið
    if st.session_state.get(DETAIL_KEY) not in visible:
        st.session_state[DETAIL_KEY] = rows[0].key
    choice = st.selectbox(
        "Nánar um tækifæri", list(visible), format_func=lambda k: detail_label(visible[k]), key=DETAIL_KEY
    )
    with st.container(border=True):
        render_details(visible[choice], now)


def render_data_quality(report: ScanReport) -> None:
    with st.expander(f"Samsvaranir til yfirferðar ({len(report.review)}) – ekki notaðar sjálfkrafa"):
        if report.review:
            st.dataframe(
                [{"Síða": r.bookmaker_id, "Leikur": r.description, "Ástæða": r.reason} for r in report.review],
                hide_index=True, width="stretch",
            )
        else:
            st.write("Ekkert til yfirferðar.")

    with st.expander(f"Útilokaðir stuðlar ({len(report.excluded)})"):
        if report.excluded:
            st.dataframe(
                [
                    {"Leikur": x.match_label, "Síða": x.bookmaker_id, "Markaður": x.market_label,
                     "Valkostur": x.outcome, "Ástæða": x.reason}
                    for x in report.excluded
                ],
                hide_index=True, width="stretch",
            )
        else:
            st.write("Engir stuðlar útilokaðir.")
