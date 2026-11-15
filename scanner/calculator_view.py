"""Streamlit-hluti fyrir handvirka arbitrage-reiknivél með framkvæmdarhæfnimati."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

import streamlit as st

from scanner.arbitrage import to_decimal
from scanner.config import BOOKMAKERS, MARKETS, Settings
from scanner.feasibility import assess_feasibility
from scanner.models import DataOrigin, StakeLimits
from scanner.ui_common import feasibility_metrics, limits_rows, reasons_list, side_rows, status_banner

CURRENCIES = ["ISK", "EUR", "USD", "UNKNOWN"]


def outcome_labels(market: str) -> tuple[str, str]:
    if market.startswith("Over/Under"):
        line = market.split()[-1]
        return f"Over {line}", f"Under {line}"
    return "BTTS Já", "BTTS Nei"


def _dec(value) -> Decimal | None:
    return None if value is None else to_decimal(value)


def _limits_inputs(side: str, now: datetime) -> StakeLimits:
    """Handvirk veðmálamörk. Tómur reitur = UNKNOWN."""
    with st.expander(f"Veðmálamörk – {side} (tómt = UNKNOWN)", expanded=False):
        currency = st.selectbox("Gjaldmiðill reiknings", CURRENCIES, key=f"cur_{side}")
        c1, c2 = st.columns(2)
        max_stake = c1.number_input("Hámarksveðmál", min_value=0.0, value=None, step=1000.0, key=f"max_{side}")
        balance = c2.number_input("Tiltæk inneign", min_value=0.0, value=None, step=1000.0, key=f"bal_{side}")
        min_stake = c1.number_input("Lágmarksveðmál", min_value=0.0, value=None, step=10.0, key=f"min_{side}")
        step = c2.number_input("Veðmálaskref", min_value=0.01, value=None, step=1.0, key=f"step_{side}",
                               help="Minnsta eining sem má leggja, t.d. 1 kr., 10 kr. eða 0,01 EUR")
        commission = c1.number_input("Þóknun af vinningi (%)", min_value=0.0, max_value=99.0, value=None,
                                     step=0.5, key=f"comm_{side}")
        fee = c2.number_input("Föst gjöld á veðmál", min_value=0.0, value=None, step=10.0, key=f"fee_{side}")
        confirmed = st.checkbox("Ég staðfesti þessar upplýsingar núna", key=f"confirmed_{side}")
    return StakeLimits(
        source=DataOrigin.MANUAL,
        currency=None if currency == "UNKNOWN" else currency,
        max_stake=_dec(max_stake),
        min_stake=_dec(min_stake),
        available_balance=_dec(balance),
        stake_step=_dec(step),
        commission_pct=_dec(commission),
        fixed_fee=_dec(fee),
        last_confirmed_at=now if confirmed else None,
    )


def render_calculator(settings: Settings, fx_rates: Mapping[str, Decimal]) -> None:
    now = datetime.now(UTC)
    st.subheader("Arbitrage-reiknivél")
    st.caption(
        "Sláðu inn tvo andstæða stuðla og veðmálamörk. Allar upplýsingar hér eru handvirkar. "
        "Reiknivélin setur engin veðmál."
    )

    top1, top2 = st.columns([1, 1])
    total = top1.number_input(
        "Heildarfjárhæð (kr.)", min_value=2, value=settings.total_stake_isk, step=1000, key="total"
    )
    market = top2.selectbox("Markaður", MARKETS, index=1, key="market")
    label_a, label_b = outcome_labels(market)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"#### A · {label_a}")
        book_a = st.selectbox("Veðmálasíða", BOOKMAKERS, index=0, key="book_A")
        odds_a = st.text_input("Stuðull", value="2,10", key="odds_A")
        limits_a = _limits_inputs("A", now)
    with col_b:
        st.markdown(f"#### B · {label_b}")
        book_b = st.selectbox("Veðmálasíða", BOOKMAKERS, index=2, key="book_B")
        odds_b = st.text_input("Stuðull", value="2,05", key="odds_B")
        limits_b = _limits_inputs("B", now)

    try:
        f = assess_feasibility(odds_a, odds_b, limits_a, limits_b, total, now=now, fx_rates=fx_rates)
    except (ValueError, TypeError) as exc:
        st.error(f"Ógildur innsláttur: {exc}")
        return

    st.divider()
    status_banner(f)
    feasibility_metrics(f)
    names = (f"{label_a} hjá {book_a}", f"{label_b} hjá {book_b}")
    st.table(side_rows(f, names))
    reasons_list(f)
    with st.expander("Skráð veðmálamörk"):
        st.table([limits_rows(names[0], limits_a, now), limits_rows(names[1], limits_b, now)])
