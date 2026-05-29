from __future__ import annotations

from indicesbot.config import IndicesConfig
from indicesbot.indicators import atr, closes, ema, rsi
from indicesbot.models import Candle, IndexQuote, MarketRegime, Opportunity


def score_trend_pullback(config: IndicesConfig, symbol: str, instrument: str, direction: str, quote: IndexQuote, candles_m15: list[Candle], candles_h1: list[Candle], candles_h4: list[Candle], regime: MarketRegime, macro_state: dict, reasons: list[str]) -> Opportunity | None:
    strategy = "TREND_PULLBACK"
    if len(candles_m15) < 30 or len(candles_h1) < 50:
        reasons.append(f"{strategy}:insufficient_candles")
        return None
    if direction == "LONG" and regime.trend != "BULL":
        reasons.append(f"{strategy}:long_trend_not_bull")
        return None
    if direction == "SHORT" and regime.trend != "BEAR":
        reasons.append(f"{strategy}:short_trend_not_bear")
        return None
    values = closes(candles_m15)
    atr_value = atr(candles_m15, 14)
    fast = ema(values, 20)
    last = values[-1]
    momentum = rsi(values, 14)
    if direction == "LONG" and not (last >= fast and 40 <= momentum <= 72):
        reasons.append(f"{strategy}:long_pullback_not_confirmed")
        return None
    if direction == "SHORT" and not (last <= fast and 28 <= momentum <= 62):
        reasons.append(f"{strategy}:short_pullback_not_confirmed")
        return None
    entry = quote.ask if direction == "LONG" else quote.bid
    stop = entry - atr_value * 1.4 if direction == "LONG" else entry + atr_value * 1.4
    target = entry + atr_value * 2.4 if direction == "LONG" else entry - atr_value * 2.4
    return Opportunity(symbol, instrument, direction, strategy, 76.0, entry, stop, target, atr_value, 1.7, 1.0, "Trend pullback aligned across timeframes", {"rsi": momentum, "spread_atr": quote.spread / atr_value if atr_value else 99})
