"""Samantekt fyrir mælaborðið, reiknuð úr arbitrage-leit."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scanner.demo_data import DEMO_FX_RATES, build_demo_events, demo_bookmakers
from scanner.models import Bookmaker
from scanner.scan import ScanReport, scan_events


@dataclass(frozen=True)
class DashboardSummary:
    match_count: int
    arbitrage_count: int
    meets_count: int  # tækifæri sem uppfylla skráð fjárhæðarskilyrði
    best_margin_pct: Decimal | None  # None ef engin tækifæri fundust
    last_updated: datetime


def summarize(report: ScanReport, now: datetime) -> DashboardSummary:
    arbs = report.arbitrage
    return DashboardSummary(
        match_count=report.match_count,
        arbitrage_count=len(arbs),
        meets_count=sum(o.feasibility.meets_conditions for o in arbs),
        best_margin_pct=max((o.margin_pct for o in arbs), default=None),
        last_updated=report.last_updated or now,
    )


def run_demo_scan(
    now: datetime,
    total_stake: int | Decimal = 100_000,
    bookmakers: dict[str, Bookmaker] | None = None,
    fx_rates: Mapping[str, Decimal] | None = None,
    anchor: datetime | None = None,
) -> ScanReport:
    """Keyrir arbitrage-leit á DEMO-gögnum. Engin API-tenging."""
    return scan_events(
        build_demo_events(now, anchor=anchor),
        bookmakers if bookmakers is not None else demo_bookmakers(now),
        now,
        total_stake,
        fx_rates=fx_rates if fx_rates is not None else DEMO_FX_RATES,
    )
