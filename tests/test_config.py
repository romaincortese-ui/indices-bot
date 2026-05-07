import pytest

from indicesbot.config import IndicesConfig, env_bool


def test_config_defaults_safe(monkeypatch) -> None:
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    config = IndicesConfig.from_env()

    assert config.execution_mode == "paper"
    assert config.paper_trade is True
    assert config.live_trading_enabled is False
    assert "SPX500" in config.universe


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
