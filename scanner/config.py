"""Stillingar forritsins, lesnar úr umhverfisbreytum (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

BOOKMAKERS: tuple[str, ...] = ("Coolbet", "bet365", "Epicbet")

MARKETS: tuple[str, ...] = (
    "Over/Under 1.5",
    "Over/Under 2.5",
    "Over/Under 3.5",
    "BTTS (Yes/No)",
)

DEFAULT_TOTAL_STAKE_ISK = 100_000
VALID_MODES = ("DEMO", "LIVE")


@dataclass(frozen=True)
class Settings:
    app_mode: str = "DEMO"
    total_stake_isk: int = DEFAULT_TOTAL_STAKE_ISK
    database_url: str = ""

    @property
    def is_demo(self) -> bool:
        return self.app_mode == "DEMO"


def load_settings() -> Settings:
    """Les stillingar úr .env. Ógild gildi falla aftur á örugg sjálfgefin gildi."""
    load_dotenv()

    mode = os.getenv("APP_MODE", "DEMO").strip().upper()
    # LIVE er ekki leyft fyrr en staðfestar API-tengingar eru komnar.
    if mode not in VALID_MODES or mode == "LIVE":
        mode = "DEMO"

    try:
        stake = int(os.getenv("DEFAULT_TOTAL_STAKE_ISK", DEFAULT_TOTAL_STAKE_ISK))
    except ValueError:
        stake = DEFAULT_TOTAL_STAKE_ISK
    if stake <= 0:
        stake = DEFAULT_TOTAL_STAKE_ISK

    from scanner.db import DEFAULT_DATABASE_URL

    database_url = os.getenv("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL
    return Settings(app_mode=mode, total_stake_isk=stake, database_url=database_url)
