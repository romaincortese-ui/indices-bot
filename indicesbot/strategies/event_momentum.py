from __future__ import annotations

from indicesbot.config import IndicesConfig
from indicesbot.indicators import atr, body_atr_ratio, candle_direction
from indicesbot.models import Candle, IndexQuote, MarketRegime, Opportunity


def score_event_momentum(config: IndicesConfig, symbol: str, instrument: str, direction: str, quote: IndexQuote, candles_m15: list[Candle], candles_h1: list[Candle], candles_h4: list[Candle], regime: MarketRegime, macro_state: dict, reasons: list[str]) -> Opportunity | None:
    strategy = "EVENT_MOMENTUM"
    scores = macro_state.get("event_scores", []) if isinstance(macro_state.get("event_scores"), list) else []
    region_scores = [row for row in scores if isinstance(row, dict) and str(row.get("region", "")).upper() in {regime.region, "GLOBAL"}]
    if not region_scores:
        reasons.append(f"{strategy}:no_event_score")
        return None
    best = max(region_scores, key=lambda row: abs(float(row.get("score", 0.0) or 0.0)))
    score_value = float(best.get("score", 0.0) or 0.0)
    if direction == "LONG" and score_value <= 0.25:
        reasons.append(f"{strategy}:long_event_not_risk_on")
        return None
    if direction == "SHORT" and score_value >= -0.25:
        reasons.append(f"{strategy}:short_event_not_risk_off")
        return None
    atr_value = atr(candles_m15, 14)
    impulse = body_atr_ratio(candles_m15, atr_value)
    if impulse < 0.45:
        reasons.append(f"{strategy}:impulse_missing")
        return None
    last_direction = candle_direction(candles_m15[-1]) if candles_m15 else "FLAT"
    if direction == "LONG" and last_direction != "UP":
        reasons.append(f"{strategy}:long_price_not_confirmed")
        return None
    if direction == "SHORT" and last_direction != "DOWN":
        reasons.append(f"{strategy}:short_price_not_confirmed")
        return None
    entry = quote.ask if direction == "LONG" else quote.bid
    stop = entry - atr_value * 1.6 if direction == "LONG" else entry + atr_value * 1.6
    target = entry + atr_value * 2.6 if direction == "LONG" else entry - atr_value * 2.6
    return Opportunity(symbol, instrument, direction, strategy, 78.0 + abs(score_value) * 10.0, entry, stop, target, atr_value, 1.6, 0.85, "Event surprise and price impulse agree", {"event_score": score_value, "spread_atr": quote.spread / atr_value if atr_value else 99})
