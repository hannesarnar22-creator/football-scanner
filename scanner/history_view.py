"""Streamlit-flipi: Saga og greining."""

from __future__ import annotations

from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

from scanner.analytics import (
    PERIODS,
    MarginPoint,
    daily_new_counts,
    is_hourly,
    load_history,
    margin_series,
    summarize_history,
    to_csv,
)
from scanner.db import Database, DataMode
from scanner.formatting import format_isk, format_number, format_percent

CHART_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=70, b=10),
    height=340,
    legend=dict(orientation="h", y=1.0, yanchor="bottom", x=1.0, xanchor="right"),
)


def _pct(v) -> str:
    return "–" if v is None else format_percent(v)


MAX_TICK_LABELS = 8


def _tick_text(label: str) -> str:
    """Tvær línur: '31.10. 12:00' -> '31.10.<br>12:00', '01.11.2026' -> '01.11.<br>2026'."""
    if " " in label:
        return label.replace(" ", "<br>")
    return f"{label[:6]}<br>{label[6:]}" if len(label) == 10 else label


def _category_axis(fig: go.Figure, labels: list[str], title: str) -> None:
    """Flokkaás með læsilegum merkingum: í mesta lagi ~8 merkingar, alltaf í réttri röð."""
    every = max(1, -(-len(labels) // MAX_TICK_LABELS))  # deiling upp á við
    shown = labels[::-1][::every][::-1]  # síðasti (nýjasti) flokkurinn er alltaf merktur
    fig.update_xaxes(
        type="category", categoryorder="array", categoryarray=labels,
        tickmode="array", tickvals=shown, ticktext=[_tick_text(label) for label in shown],
        tickangle=0, title_text=title,
    )


def margin_figure(points: list[MarginPoint], hourly: bool) -> go.Figure:
    """Línurit yfir framlegð. Tímabil (klst. eða dagar) sem flokkar með læsilegum merkingum."""
    labels = [p.bucket.label for p in points]
    counts = [p.count for p in points]
    fig = go.Figure()
    for name, attr in (("Meðalframlegð", "avg_margin_pct"), ("Hæsta framlegð", "max_margin_pct")):
        fig.add_scatter(
            x=labels,
            y=[None if getattr(p, attr) is None else float(getattr(p, attr)) for p in points],
            customdata=counts,
            mode="lines+markers",
            name=name,
            connectgaps=False,  # rof þar sem engin tækifæri voru – ekki 0%
            hovertemplate="%{x} UTC<br>" + name + ": %{y:.2f}%<br>Tækifæri: %{customdata}<extra></extra>",
        )
    fig.update_layout(title="Arbitrage-framlegð" + (" eftir klukkustundum" if hourly else " eftir dögum"),
                      yaxis_title="Framlegð (%)", **CHART_LAYOUT)
    _category_axis(fig, labels, "Tími (UTC)" if hourly else "Dagsetning (UTC)")
    fig.update_yaxes(rangemode="tozero", ticksuffix="%")
    return fig


def counts_figure(counts: list[tuple[str, int]]) -> go.Figure:
    """Súlurit: dagsetningar sem flokkar (ekki samfelldur tímaás)."""
    labels = [d for d, _ in counts]
    fig = go.Figure(go.Bar(
        x=labels, y=[n for _, n in counts], name="Ný tækifæri",
        text=[n or "" for _, n in counts], textposition="outside",
        hovertemplate="%{x}<br>Ný tækifæri: %{y}<extra></extra>",
    ))
    fig.update_layout(title="Ný einstök tækifæri eftir dögum", yaxis_title="Fjöldi", **CHART_LAYOUT)
    _category_axis(fig, labels, "Dagsetning (UTC)")
    peak = max((n for _, n in counts), default=0)
    fig.update_yaxes(rangemode="tozero", dtick=1 if peak <= 10 else None, range=[0, max(peak, 1) * 1.15])
    return fig


def render_history(db: Database, now: datetime, mode: DataMode) -> None:
    """`mode` kemur úr gagnahamnum í hliðarstikunni – DEMO og LIVE aldrei blandað."""
    st.subheader("Saga og greining")
    period_label = st.radio("Tímabil", list(PERIODS), horizontal=True, key="history_period")
    period = PERIODS[period_label]

    if mode is DataMode.DEMO:
        st.warning(
            "🧪 **DEMO-saga** – tilbúnir leikir og stuðlar. Þetta eru **ekki** raunveruleg tækifæri "
            "og eru aldrei talin með LIVE-gögnum."
        )
    else:
        st.info("✅ LIVE-saga – aðeins gögn úr staðfestum API-tengingum.")

    items = load_history(db, mode, now, period)
    if not items:
        st.info(f"Engin {mode.value}-tækifæri skráð á tímabilinu ({period_label.lower()}).")
        return

    s = summarize_history(items)
    r1 = st.columns(4)
    r1[0].metric("Einstök tækifæri", s.unique_count)
    r1[1].metric("Yfir 2%", s.over_2)
    r1[2].metric("Yfir 3%", s.over_3)
    r1[3].metric("Yfir 5%", s.over_5)
    r2 = st.columns(4)
    r2[0].metric("Meðalframlegð", _pct(s.avg_margin_pct))
    r2[1].metric("Hæsta framlegð", _pct(s.max_margin_pct))
    r2[2].metric("Uppfylla 100.000 kr. skilyrði", s.meets_100k_count)
    r2[3].metric("Óstaðfest", s.unconfirmed_count)
    st.caption("Mælingar byggja á nýjustu skráðu gildum hvers einstaks tækifæris. Tími í UTC.")

    g1, g2 = st.columns(2)
    g1.plotly_chart(margin_figure(margin_series(items, now, period), is_hourly(period)),
                    width="stretch", key="chart_margin")
    g2.plotly_chart(counts_figure(daily_new_counts(items, now, period)), width="stretch", key="chart_counts")
    st.caption(
        "Framlegð: tækifæri sem voru til á hverju tímabili, með framlegð eins og hún var skráð þá "
        + ("(eftir klukkustundum)." if is_hourly(period) else "(eftir dögum).")
        + " Ný tækifæri: talin þann dag sem þau sáust fyrst. Allir tímar í UTC."
    )

    st.markdown("**Söguleg tækifæri**")
    st.dataframe(
        [
            {
                "Leikur": i.match,
                "Keppni": i.competition,
                "Markaður": i.market,
                "Veðmálasíður": f"{i.bookmaker_a} ({i.outcome_a}) / {i.bookmaker_b} ({i.outcome_b})",
                "Stuðlar": f"{format_number(i.odds_a)} / {format_number(i.odds_b)}",
                "Framlegð": format_percent(i.margin_pct),
                "Hæsta framlegð": format_percent(i.peak_margin_pct),
                "Raunframlegð": _pct(i.real_margin_pct),
                "Lægsti hagnaður": "–" if i.worst_case_profit is None else format_isk(i.worst_case_profit, 2),
                "Staða": i.status,
                "Fyrst séð": f"{i.first_seen_at:%d.%m. %H:%M}",
                "Síðast séð": f"{i.last_seen_at:%d.%m. %H:%M}",
                "Virkt": "Já" if i.is_active else "Nei",
                "Athuganir": i.observations,
                "Hamur": i.mode,
            }
            for i in items
        ],
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "⬇️ Sækja CSV",
        data=to_csv(items),
        file_name=f"arbitrage_saga_{mode.value.lower()}_{now:%Y%m%d}.csv",
        mime="text/csv",
        key="history_csv",
    )
