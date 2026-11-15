import pytest

from scanner import config
from scanner.config import BOOKMAKERS, MARKETS, load_settings


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    # Láta prófanir ekki lesa raunverulega .env skrá
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.delenv("DEFAULT_TOTAL_STAKE_ISK", raising=False)


def test_defaults():
    s = load_settings()
    assert s.app_mode == "DEMO"
    assert s.is_demo
    assert s.total_stake_isk == 100_000


def test_live_mode_is_blocked_in_phase_1(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    assert load_settings().app_mode == "DEMO"


@pytest.mark.parametrize("value", ["abc", "-5", "0", ""])
def test_invalid_stake_falls_back(monkeypatch, value):
    monkeypatch.setenv("DEFAULT_TOTAL_STAKE_ISK", value)
    assert load_settings().total_stake_isk == 100_000


def test_custom_stake(monkeypatch):
    monkeypatch.setenv("DEFAULT_TOTAL_STAKE_ISK", "250000")
    assert load_settings().total_stake_isk == 250_000


def test_bookmakers_and_markets():
    assert set(BOOKMAKERS) == {"Coolbet", "bet365", "Epicbet"}
    assert len(MARKETS) == 4
