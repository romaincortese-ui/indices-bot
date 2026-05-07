from __future__ import annotations

from indicesbot.config import IndicesConfig
from indicesbot.indicators import atr, bollinger, closes, rsi
from indicesbot.models import Candle, IndexQuote, MarketRegime, Opportunity


def score_mean_reversion(config: IndicesConfig, symbol: str, instrument: str, direction: str, quote: IndexQuote, candles_m15: list[Candle], candles_h1: list[Candle], candles_h4: list[Candle], regime: MarketRegime, macro_state: dict, reasons: list[str]) -> Opportunity | None:
    strategy = "MEAN_REVERSION"
    if regime.volatility in {"HIGH", "EXTREME"}:
        reasons.append(f"{strategy}:volatility_too_high")
        return None
    values = closes(candles_m15)
    if len(values) < 25:
        reasons.append(f"{strategy}:insufficient_candles")
        return None
    lower, mid, upper = bollinger(values, 20, 2.0)
    momentum = rsi(values, 14)
    atr_value = atr(candles_m15, 14)
    last = values[-1]
    if direction == "LONG" and not (last <= lower and momentum < 35):
        reasons.append(f"{strategy}:long_not_oversold")
        return None
    if direction == "SHORT" and not (last >= upper and momentum > 65):
        reasons.append(f"{strategy}:short_not_overbought")
        return None
    entry = quote.ask if direction == "LONG" else quote.bid
    stop = entry - atr_value * 1.1 if direction == "LONG" else entry + atr_value * 1.1
    return Opportunity(symbol, instrument, direction, strategy, 72.0, entry, stop, mid, atr_value, 1.2, 0.75, "Mean reversion at volatility band extreme", {"rsi": momentum, "spread_atr": quote.spread / atr_value if atr_value else 99})
