from dataclasses import replace
from datetime import datetime, timezone

from indicesbot.config import IndicesConfig
from indicesbot.models import AccountSummary, IndexPosition, InstrumentDetails, Opportunity
from indicesbot.risk import can_open, position_from_opportunity


def test_position_sizing_respects_margin_cap() -> None:
    config = IndicesConfig.from_env()
    account = AccountSummary(10000, 10000, 5000, 0, "USD")
    details = InstrumentDetails("SPX500_USD", margin_rate=0.05)
    opportunity = Opportunity("SPX500", "SPX500_USD", "LONG", "TEST", 80, 5000, 4975, 5050, 20, 2, 1, "test", {})

    position = position_from_opportunity(opportunity, config, account, details, 1.0)

    assert position.units <= 4
    assert position.order_units > 0


def test_can_open_blocks_same_symbol() -> None:
    config = IndicesConfig.from_env()
    position = IndexPosition("SPX500", "SPX500_USD", "LONG", "TEST", 1, 1, 5000, 4975, 5050, datetime.now(timezone.utc), "US", "1", {})
    state = {"open_positions": [{"symbol": "SPX500", "region": "US"}]}

    ok, reason = can_open(position, state, config)

    assert ok is False
    assert reason == "max_open_per_symbol"


def test_min_unit_floor_is_guarded() -> None:
    config = replace(IndicesConfig.from_env(), min_unit_floor_enabled=True, min_unit_floor_max_risk_nav_pct=0.001)
    account = AccountSummary(10000, 10000, 10000, 0, "USD")
    details = InstrumentDetails("FR40_EUR", margin_rate=0.02)
    opportunity = Opportunity("FR40", "FR40_EUR", "LONG", "TEST", 80, 7000, 6995, 7010, 5, 2, 0.01, "test", {})

    position = position_from_opportunity(opportunity, config, account, details, 1.0)

    assert position.units == 1
    assert position.metadata["min_unit_floor_applied"] is True


def test_fractional_index_unit_floor_uses_oanda_precision() -> None:
    config = replace(
        IndicesConfig.from_env(),
        max_risk_per_trade=0.005,
        max_total_indices_risk=0.05,
        max_margin_per_entry_pct=1.0,
        min_unit_floor_enabled=True,
        min_unit_floor_max_risk_nav_pct=0.02,
    )
    account = AccountSummary(20, 20, 20, 0, "GBP")
    details = InstrumentDetails("SPX500_USD", trade_units_precision=2, margin_rate=0.05, minimum_trade_size=0.01)
    opportunity = Opportunity("SPX500", "SPX500_USD", "LONG", "TEST", 80, 7400, 7350, 7500, 20, 2, 1, "test", {})

    position = position_from_opportunity(opportunity, config, account, details, 0.75)
    ok, reason = can_open(position, {"open_positions": []}, config)

    assert position.units == 0.01
    assert position.order_units == 0.01
    assert position.metadata["min_unit_floor_applied"] is True
    assert position.metadata["target_risk_amount"] == 0.1
    assert position.metadata["risk_nav_pct"] == 0.01875
    assert ok is True
    assert reason == "ok"


def test_can_open_blocks_total_indices_risk() -> None:
    config = replace(IndicesConfig.from_env(), max_total_indices_risk=0.006, max_open_per_symbol=3, max_open_per_region=3)
    position = IndexPosition("NAS100", "NAS100_USD", "LONG", "TEST", 1, 1, 5000, 4975, 5050, datetime.now(timezone.utc), "US", "2", {"risk_nav_pct": 0.0035})
    state = {"open_positions": [{"symbol": "SPX500", "region": "US", "metadata": {"risk_nav_pct": 0.003}}]}

    ok, reason = can_open(position, state, config)

    assert ok is False
    assert reason == "max_total_indices_risk"
