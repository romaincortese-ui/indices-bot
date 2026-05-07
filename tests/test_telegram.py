from datetime import datetime, timezone

from indicesbot.models import IndexPosition, Opportunity
from indicesbot.telegram import help_message, opportunity_message, order_opened_message, order_rejected_message, startup_message, status_message, trade_closed_message


def test_telegram_messages_are_clear() -> None:
    startup = startup_message(mode="paper", universe=("SPX500", "NAS100"), account_label="practice")
    rejected = order_rejected_message(symbol="SPX500", direction="LONG", strategy="TEST", reason="spread_too_wide")
    opportunity = opportunity_message(Opportunity("SPX500", "SPX500_USD", "LONG", "TEST", 80, 5000, 4975, 5050, 20, 2, 1, "Clean setup", {}), mode="paper")
    position = IndexPosition("SPX500", "SPX500_USD", "LONG", "TEST", 2, 2, 5000, 4975, 5050, datetime.now(timezone.utc), "US", "T1")
    opened = order_opened_message(position)
    closed = trade_closed_message({"symbol": "SPX500", "instrument": "SPX500_USD", "direction": "LONG", "strategy": "TEST", "order_id": "T1"}, reason="not_in_oanda_open_positions")
    status = status_message({"updated_at": "2026-05-07T12:00:00+00:00", "last_scan": {"status": "blocked", "blocked_by": "score_below_threshold", "symbol": "SPX500"}, "signals_seen": 3, "open_positions": [{"symbol": "SPX500", "direction": "LONG", "strategy": "TEST", "entry_price": 5000, "stop_price": 4975, "take_profit_price": 5050}]})
    help_text = help_message()

    assert "Indices Bot Boot" in startup
    assert "Universe: 2 indices" in startup
    assert "Reason: spread too wide" in rejected
    assert "Direction: LONG" in opportunity
    assert "Score: 80.0 | Risk/reward: 2.00" in opportunity
    assert "Indices Trade Opened" in opened
    assert "Order ID: T1" in opened
    assert "Reason: not in oanda open positions" in closed
    assert "Bot: Running" in status
    assert "Last blocker: score below threshold" in status
    assert "SPX500 LONG | TEST" in status
    assert "/closeall - record a close-all request for operator follow-up" in help_text
