import pytest

from indicesbot.config import IndicesConfig, env_bool


def test_config_defaults_safe(monkeypatch) -> None:
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    config = IndicesConfig.from_env()

    assert config.execution_mode == "paper"
    assert config.paper_trade is True
    assert config.live_trading_enabled is False
    assert "SPX500" in config.universe
    assert config.oanda_instrument_for("DE40") == "DE30_EUR"
    assert config.enabled_strategies == ("TREND_PULLBACK", "OPENING_RANGE_BREAKOUT")
    assert config.require_calibration_for_trading is True
    assert config.startup_message_cooldown_minutes == 30
    assert config.risk_on_enabled_strategies == ("OPENING_RANGE_BREAKOUT",)
    assert config.risk_off_aggressive_enabled is False
    assert config.risk_off_aggressive_enabled_strategies == ("EVENT_MOMENTUM", "OPENING_RANGE_BREAKOUT")
    assert config.min_unit_floor_enabled is False


def test_invalid_bool_raises(monkeypatch) -> None:
    monkeypatch.setenv("TEST_BOOL", "maybe")

    with pytest.raises(ValueError):
        env_bool("TEST_BOOL")


def test_live_requires_credentials_and_telegram(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("PAPER_TRADE", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)

    config = IndicesConfig.from_env()
    with pytest.raises(RuntimeError):
        config.validate_for_live()


def test_enabled_strategies_env(monkeypatch) -> None:
    monkeypatch.setenv("INDICES_ENABLED_STRATEGIES", "trend_pullback event_momentum")

    config = IndicesConfig.from_env()

    assert config.enabled_strategies == ("TREND_PULLBACK", "EVENT_MOMENTUM")


def test_risk_off_aggressive_env(monkeypatch) -> None:
    monkeypatch.setenv("RISK_ON_ENABLED_STRATEGIES", "OPENING_RANGE_BREAKOUT EVENT_MOMENTUM")
    monkeypatch.setenv("RISK_OFF_AGGRESSIVE_ENABLED", "true")
    monkeypatch.setenv("RISK_OFF_AGGRESSIVE_ENABLED_STRATEGIES", "EVENT_MOMENTUM")
    monkeypatch.setenv("RISK_OFF_AGGRESSIVE_MIN_SCORE", "73")
    monkeypatch.setenv("INDICES_MIN_UNIT_FLOOR_ENABLED", "true")

    config = IndicesConfig.from_env()

    assert config.risk_on_enabled_strategies == ("OPENING_RANGE_BREAKOUT", "EVENT_MOMENTUM")
    assert config.risk_off_aggressive_enabled is True
    assert config.risk_off_aggressive_enabled_strategies == ("EVENT_MOMENTUM",)
    assert config.risk_off_aggressive_min_score == 73
    assert config.min_unit_floor_enabled is True
