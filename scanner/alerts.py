"""Innbyggt viðvörunarkerfi fyrir ný arbitrage-tækifæri.

  - Viðvörun verður til þegar tækifæri nær viðvörunarmörkum (sjálfgefið 5%).
  - Hvert tækifæri (stöðugt auðkenni úr áfanga 5) fær í mesta lagi EINA viðvörun
    í hverjum ham – endurhleðsla, uppfærsla eða endurræsing endurtekur hana ekki.
  - Viðvaranir eru vistaðar í SQLite og tengdar við tækifærið.
  - DEMO og LIVE eru aðskilin. Engin tölvupóstur, SMS eða push-tilkynning er send.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update

from scanner.db import AlertRow, Database, DataMode, OpportunityRow
from scanner.history import mode_of_quote, opportunity_key
from scanner.risk import assess_risk
from scanner.scan import ScanReport

DEFAULT_ALERT_THRESHOLD = Decimal(5)


@dataclass(frozen=True)
class Alert:
    id: int
    mode: str
    opp_key: str
    created_at: datetime
    threshold_pct: Decimal
    match_label: str
    competition: str
    market_label: str
    bookmaker_a: str
    bookmaker_b: str
    outcome_a: str
    outcome_b: str
    odds_a: Decimal
    odds_b: Decimal
    margin_pct: Decimal
    worst_case_profit: Decimal | None
    total_stake: Decimal
    status: str
    odds_age_seconds: int
    is_read: bool

    @property
    def is_demo(self) -> bool:
        return self.mode == DataMode.DEMO


def _to_alert(r: AlertRow) -> Alert:
    return Alert(**{f: getattr(r, f) for f in Alert.__dataclass_fields__})


def generate_alerts(
    db: Database,
    report: ScanReport,
    mode: DataMode,
    now: datetime,
    threshold_pct: Decimal = DEFAULT_ALERT_THRESHOLD,
) -> list[Alert]:
    """Býr til viðvaranir fyrir tækifæri ≥ mörkum sem hafa ekki fengið viðvörun áður.

    Krefst þess að leitin hafi verið vistuð í sögu (ingest_scan) svo hægt sé að
    tengja viðvörunina við tækifærið. Skilar aðeins NÝJUM viðvörunum.
    """
    threshold_pct = Decimal(str(threshold_pct))
    candidates = [o for o in report.arbitrage if o.margin_pct >= threshold_pct]
    for o in candidates:
        for q in (o.quote_a, o.quote_b):
            if mode_of_quote(q) is not mode:
                raise ValueError(f"Stuðull í ham {mode_of_quote(q)} – DEMO og LIVE má ekki blanda saman")
    if not candidates:
        return []

    created: list[AlertRow] = []
    with db.session() as s, s.begin():
        alerted = set(s.scalars(select(AlertRow.opp_key).where(AlertRow.mode == mode)))
        opp_ids = dict(s.execute(select(OpportunityRow.opp_key, OpportunityRow.id).where(OpportunityRow.mode == mode)).all())
        for o in candidates:
            key = opportunity_key(o)
            if key in alerted:
                continue
            if key not in opp_ids:
                raise LookupError("Tækifæri finnst ekki í sögu – vistaðu leitina (ingest_scan) fyrst")
            risk = assess_risk(o, now)
            f = o.feasibility
            row = AlertRow(
                mode=mode, opp_key=key, opportunity_id=opp_ids[key], created_at=now, threshold_pct=threshold_pct,
                match_label=o.match.label, competition=o.match.competition_name, market_label=o.market.label,
                bookmaker_a=o.bookmaker_a.name, bookmaker_b=o.bookmaker_b.name,
                outcome_a=o.quote_a.outcome.value, outcome_b=o.quote_b.outcome.value,
                odds_a=o.quote_a.price, odds_b=o.quote_b.price, margin_pct=o.margin_pct,
                worst_case_profit=f.worst_case_profit, total_stake=f.requested_total,
                status=risk.status.value, odds_age_seconds=int(risk.odds_age.total_seconds()), is_read=False,
            )
            s.add(row)
            alerted.add(key)
            created.append(row)
        s.flush()
        return [_to_alert(r) for r in created]


def list_alerts(db: Database, mode: DataMode, only_unread: bool = False, limit: int = 50) -> list[Alert]:
    q = select(AlertRow).where(AlertRow.mode == mode).order_by(AlertRow.created_at.desc(), AlertRow.id.desc())
    if only_unread:
        q = q.where(AlertRow.is_read.is_(False))
    with db.session() as s:
        return [_to_alert(r) for r in s.scalars(q.limit(limit))]


def mark_all_read(db: Database, mode: DataMode) -> int:
    with db.session() as s, s.begin():
        result = s.execute(update(AlertRow).where(AlertRow.mode == mode, AlertRow.is_read.is_(False)).values(is_read=True))
        return result.rowcount
