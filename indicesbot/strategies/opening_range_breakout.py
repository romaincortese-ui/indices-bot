from __future__ import annotations

from indicesbot.config import IndicesConfig
from indicesbot.indicators import atr, body_atr_ratio, candle_direction
from indicesbot.models import Candle, IndexQuote, MarketRegime, Opportunity


def score_opening_range_breakout(config: IndicesConfig, symbol: str, instrument: str, direction: str, quote: IndexQuote, candles_m15: list[Candle], candles_h1: list[Candle], candles_h4: list[Candle], regime: MarketRegime, macro_state: dict, reasons: list[str]) -> Opportunity | None:
    strategy = "OPENING_RANGE_BREAKOUT"
    if "OPEN" not in regime.session:
        reasons.append(f"{strategy}:session_not_open")
        return None
    if len(candles_m15) < 12:
        reasons.append(f"{strategy}:insufficient_candles")
        return None
    atr_value = atr(candles_m15, 14)
    if atr_value <= 0:
        reasons.append(f"{strategy}:atr_unavailable")
        return None
    opening = candles_m15[-8:-2]
    high = max(c.high for c in opening)
    low = min(c.low for c in opening)
    last = candles_m15[-1]
    impulse = body_atr_ratio(candles_m15, atr_value)
    buffer = atr_value * 0.12
    if impulse < 0.35:
        reasons.append(f"{strategy}:impulse_missing")
        return None
    if direction == "LONG" and not (last.close > high + buffer and candle_direction(last) == "UP"):
        reasons.append(f"{strategy}:long_breakout_not_confirmed")
        return None
    if direction == "SHORT" and not (last.close < low - buffer and candle_direction(last) == "DOWN"):
        reasons.append(f"{strategy}:short_breakout_not_confirmed")
        return None
    if direction == "LONG" and regime.risk_mode == "RISK_OFF":
        reasons.append(f"{strategy}:long_against_risk_off")
        return None
    if direction == "SHORT" and regime.risk_mode == "RISK_ON":
        reasons.append(f"{strategy}:short_against_risk_on")
        return None
    entry = quote.ask if direction == "LONG" else quote.bid
    stop = entry - atr_value * 1.2 if direction == "LONG" else entry + atr_value * 1.2
    target = entry + atr_value * 2.0 if direction == "LONG" else entry - atr_value * 2.0
    return Opportunity(symbol, instrument, direction, strategy, 74.0 + impulse * 10.0, entry, stop, target, atr_value, 1.6, 1.0, "Opening range breakout confirmed", {"spread_atr": quote.spread / atr_value})
