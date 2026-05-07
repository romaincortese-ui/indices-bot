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
