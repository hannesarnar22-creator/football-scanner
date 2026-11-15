"""Sameiginlegir Streamlit-hlutar fyrir framkvæmdarhæfni."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from scanner.feasibility import DISCLAIMER, FeasibilityResult, FeasibilityStatus, SidePlan
from scanner.formatting import format_isk, format_money, format_number, format_percent
from scanner.models import DataOrigin, StakeLimits

UNKNOWN = "UNKNOWN"

STATUS_ICON = {
    FeasibilityStatus.MEETS: "🟢",
    FeasibilityStatus.UNCONFIRMED: "🟡",
    FeasibilityStatus.FAILS: "🔴",
    FeasibilityStatus.STALE_OR_INCOMPARABLE: "🟠",
}

ORIGIN_BADGE = {
    DataOrigin.DEMO: "🧪 DEMO-gögn",
    DataOrigin.MANUAL: "✍️ Handvirkar upplýsingar",
    DataOrigin.API: "✅ Staðfest API-gögn",
}


def money_or_unknown(value, currency: str | None) -> str:
    return UNKNOWN if value is None else format_money(value, currency)


def limits_rows(label: str, limits: StakeLimits, now: datetime) -> dict[str, str]:
    cur = limits.currency
    if limits.last_confirmed_at is None:
        confirmed = UNKNOWN
    else:
        minutes = int((now - limits.last_confirmed_at).total_seconds() // 60)
        confirmed = f"{limits.last_confirmed_at:%d.%m. %H:%M} UTC (fyrir {minutes} mín.)"
    return {
        "Valkostur": label,
        "Hámarksveðmál": money_or_unknown(limits.max_stake, cur),
        "Lágmarksveðmál": money_or_unknown(limits.min_stake, cur),
        "Tiltæk inneign": money_or_unknown(limits.available_balance, cur),
        "Gjaldmiðill": cur or UNKNOWN,
        "Veðmálaskref": money_or_unknown(limits.stake_step, cur),
        "Þóknun": UNKNOWN if limits.commission_pct is None else format_percent(limits.commission_pct),
        "Föst gjöld": money_or_unknown(limits.fixed_fee, cur),
        "Síðast staðfest": confirmed,
        "Uppruni": limits.source.value,
    }


def status_banner(f: FeasibilityResult) -> None:
    text = f"{STATUS_ICON[f.status]} **{f.status.value}** · Grunnur: **{ORIGIN_BADGE[f.data_basis]}**"
    match f.status:
        case FeasibilityStatus.MEETS:
            st.success(text)
            st.caption(DISCLAIMER)
        case FeasibilityStatus.UNCONFIRMED:
            st.warning(text)
        case FeasibilityStatus.FAILS:
            st.error(text)
        case FeasibilityStatus.STALE_OR_INCOMPARABLE:
            st.error(text)


def max_feasible_text(f: FeasibilityResult) -> str:
    if f.max_feasible_total is None:
        return "–"
    return format_isk(f.max_feasible_total)


def feasibility_metrics(f: FeasibilityResult) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Arbitrage", "Já" if f.is_arbitrage else "Nei")
    c2.metric("Fræðileg framlegð", format_percent(f.theoretical_margin_pct))
    c3.metric("Raunframlegð", "–" if f.real_margin_pct is None else format_percent(f.real_margin_pct))
    c4.metric("Lægsti nettóhagnaður", "–" if f.worst_case_profit is None else format_isk(f.worst_case_profit, 2))
    c5.metric("Hæsta mögulega fjárhæð", max_feasible_text(f))


def side_rows(f: FeasibilityResult, names: tuple[str, str]) -> list[dict[str, str]]:
    rows = []
    for name, side, profit in ((names[0], f.side_a, f.profit_if_a), (names[1], f.side_b, f.profit_if_b)):
        if side is None:
            rows.append({"Valkostur": name, "Stuðull": "–", "Fjárhæð": "–", "Í ISK": "–", "Hagnaður ef vinnur": "–"})
            continue
        rows.append(_side_row(name, side, profit))
    return rows


def _side_row(name: str, side: SidePlan, profit) -> dict[str, str]:
    return {
        "Valkostur": name,
        "Stuðull": format_number(side.odds),
        "Fjárhæð": format_money(side.stake, side.currency),
        "Í ISK": format_isk(side.stake_isk, 0 if side.currency == "ISK" else 2),
        "Hagnaður ef vinnur": format_isk(profit, 2),
    }


def reasons_list(f: FeasibilityResult) -> None:
    if f.reasons:
        st.markdown("**Forsendur og athugasemdir**")
        for r in f.reasons:
            st.markdown(f"- {r}")
    if f.max_feasible_total is None and f.is_arbitrage:
        st.caption(
            "Hæsta mögulega fjárhæð er ekki reiknuð: ekkert efra mark (hámark eða inneign) er skráð, "
            "eða engin fjárhæð innan marka skilar jákvæðum hagnaði."
        )
