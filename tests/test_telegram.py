from datetime import datetime, timezone

from indicesbot.models import IndexPosition, Opportunity
from indicesbot.telegram import help_message, opportunity_message, order_opened_message, order_rejected_message, startup_message, status_message, trade_closed_message


def test_telegram_messages_are_clear() -> None:
    startup = startup_message(mode="paper", universe=("SPX500", "NAS100"), account_label="practice", calibration_required=True)
    rejected = order_rejected_message(symbol="SPX500", direction="LONG", strategy="TEST", reason="spread_too_wide")
    opportunity = opportunity_message(Opportunity("SPX500", "SPX500_USD", "LONG", "TEST", 80, 5000, 4975, 5050, 20, 2, 1, "Clean setup", {}), mode="paper")
    position = IndexPosition("SPX500", "SPX500_USD", "LONG", "TEST", 2, 2, 5000, 4975, 5050, datetime.now(timezone.utc), "US", "T1", {"estimated_margin_used": 200.0, "account_currency": "GBP", "risk_amount": 50.0, "risk_nav_pct": 0.005})
    opened = order_opened_message(position)
    closed = trade_closed_message({"symbol": "SPX500", "instrument": "SPX500_USD", "direction": "LONG", "strategy": "TEST", "order_id": "T1", "realized_pl": 3.25, "margin_used": 200.0, "pnl_pct": 1.625, "account_currency": "GBP", "risk_amount": 50.0, "sync_reason": "not_in_oanda_open_positions"}, reason="not_in_oanda_open_positions")
    status = status_message({"updated_at": "2026-05-07T12:00:00+00:00", "last_scan": {"status": "blocked", "blocked_by": "score_below_threshold", "symbol": "SPX500"}, "signals_seen": 3, "open_positions": [{"symbol": "SPX500", "direction": "LONG", "strategy": "TEST", "entry_price": 5000, "stop_price": 4975, "take_profit_price": 5050}]})
    help_text = help_message()

    assert "🚀 <b>Indices Bot Online</b>" in startup
    assert "Universe: 2 indices" in startup
    assert "Calibration gate: 🛡️ On" in startup
    assert "Reason: spread too wide" in rejected
    assert "🟢 LONG SPX500" in opportunity
    assert "Score: 80.0 | Risk/reward: 2.00" in opportunity
    assert "🟢 <b>Indices Trade Opened</b>" in opened
    assert "P&L: GBP +0.00 (+0.00% of margin)" in opened
    assert "Margin used: GBP 200.00 est." in opened
    assert "Risk at stop: GBP 50.00 (+0.50% NAV)" in opened
    assert "Order ID: T1" in opened
    assert "P&L: GBP +3.25 (+1.62% of margin)" in closed
    assert "Margin used: GBP 200.00" in closed
    assert "Risk at stop: GBP 50.00" in closed
    assert "Reason: broker sync: OANDA no longer reports this trade open" in closed
    assert "Broker sync: OANDA no longer lists this trade as open" in closed
    assert "🤖 Bot: 🟢 Running" in status
    assert "Last blocker: score below threshold" in status
    assert "<b>SPX500</b> | Test" in status
    assert "/closeall - Record a close-all request for operator follow-up" in help_text