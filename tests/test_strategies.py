from datetime import datetime, timedelta, timezone

from indicesbot.config import IndicesConfig
from indicesbot.models import Candle, IndexQuote, MarketRegime
from indicesbot.strategies.event_momentum import score_event_momentum
from indicesbot.strategies import evaluate_all
from indicesbot.strategies.opening_range_breakout import score_opening_range_breakout


def _candles(direction="UP"):
    now = datetime.now(timezone.utc) - timedelta(hours=4)
    rows = []
    price = 100.0
    for index in range(40):
        open_price = price
        if direction == "UP":
            close = price + (0.2 if index < 38 else 1.0)
        else:
            close = price - (0.2 if index < 38 else 1.0)
        rows.append(Candle(now + timedelta(minutes=15 * index), open_price, max(open_price, close) + 0.2, min(open_price, close) - 0.2, close, 1000))
        price = close
    return rows


def test_opening_range_long_and_short(monkeypatch) -> None:
    config = IndicesConfig.from_env()
    quote_long = IndexQuote("SPX500", "SPX500_USD", 109, 109.1, 109.05, 0.1, True, "tradeable", datetime.now(timezone.utc))
    quote_short = IndexQuote("SPX500", "SPX500_USD", 91, 91.1, 91.05, 0.1, True, "tradeable", datetime.now(timezone.utc))
    regime = MarketRegime("SPX500", "US", "BULL", "NORMAL", "MIXED", "US_CASH_OPEN")

    long = score_opening_range_breakout(config, "SPX500", "SPX500_USD", "LONG", quote_long, _candles("UP"), [], [], regime, {}, [])
    short = score_opening_range_breakout(config, "SPX500", "SPX500_USD", "SHORT", quote_short, _candles("DOWN"), [], [], regime, {}, [])

    assert long is not None
    assert long.direction == "LONG"
    assert short is not None
    assert short.direction == "SHORT"


def test_event_momentum_requires_event_score() -> None:
    config = IndicesConfig.from_env()
    quote = IndexQuote("SPX500", "SPX500_USD", 109, 109.1, 109.05, 0.1, True, "tradeable", datetime.now(timezone.utc))
    regime = MarketRegime("SPX500", "US", "BULL", "NORMAL", "RISK_ON", "US_CASH_OPEN")
    macro = {"event_scores": [{"region": "US", "score": 0.7}]}

    opportunity = score_event_momentum(config, "SPX500", "SPX500_USD", "LONG", quote, _candles("UP"), [], [], regime, macro, [])

    assert opportunity is not None
    assert opportunity.strategy == "EVENT_MOMENTUM"


def test_default_strategy_allow_list_excludes_event_momentum() -> None:
    config = IndicesConfig.from_env()
    quote = IndexQuote("SPX500", "SPX500_USD", 109, 109.1, 109.05, 0.1, True, "tradeable", datetime.now(timezone.utc))
    regime = MarketRegime("SPX500", "US", "BULL", "NORMAL", "RISK_ON", "US_CASH_OPEN")
    macro = {"event_scores": [{"region": "US", "score": 0.7}]}
    reasons: list[str] = []

    opportunities = evaluate_all(config, "SPX500", "SPX500_USD", quote, _candles("UP"), _candles("UP"), _candles("UP"), regime, macro, reasons)

    assert all(item.strategy != "EVENT_MOMENTUM" for item in opportunities)
    assert "EVENT_MOMENTUM:disabled" in reasons
