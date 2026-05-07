from indicesbot.models import Opportunity
from indicesbot.telegram import opportunity_message, order_rejected_message, startup_message


def test_telegram_messages_are_clear() -> None:
    startup = startup_message(mode="paper", universe=("SPX500", "NAS100"), account_label="practice")
    rejected = order_rejected_message(symbol="SPX500", direction="LONG", strategy="TEST", reason="spread_too_wide")
    opportunity = opportunity_message(Opportunity("SPX500", "SPX500_USD", "LONG", "TEST", 80, 5000, 4975, 5050, 20, 2, 1, "Clean setup", {}), mode="paper")

    assert "Indices Bot started" in startup
    assert "Reason: spread_too_wide" in rejected
    assert "Direction: LONG" in opportunity
    assert "Score: 80.0" in opportunity
