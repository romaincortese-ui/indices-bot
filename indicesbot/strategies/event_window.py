"""EVENT_WINDOW: direction-agnostic post-event opening-range breakout.

Active only inside a curated event's POST window (see indicesbot.event_window).
Builds the opening range from the first ``EVENT_WINDOW_RANGE_BARS`` completed
M15 candles after the event time and trades the break of that range with the
house ATR stop/target. Direction-agnostic by design: large scheduled events
(IPO listings, CPI, FOMC) are two-sided; the lane trades the resolution, not a
prediction.
"""
from __future__ import annotations

import os

from indicesbot.config import IndicesConfig
from indicesbot.event_window import active_post_window
from indicesbot.indicators import atr
from indicesbot.models import Candle, IndexQuote, MarketRegime, Opportunity


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def score_event_window(config: IndicesConfig, symbol: str, instrument: str, direction: str, quote: IndexQuote, candles_m15: list[Candle], candles_h1: list[Candle], candles_h4: list[Candle], regime: MarketRegime, macro_state: dict, reasons: list[str]) -> Opportunity | None:
    strategy = "EVENT_WINDOW"
    window = active_post_window(quote.time, regime.region)
    if window is None:
        reasons.append(f"{strategy}:no_active_event_window")
        return None
    range_bars = max(1, _env_int("EVENT_WINDOW_RANGE_BARS", 2))
    post = [c for c in candles_m15 if c.complete and c.time >= window.occurs_at]
    if len(post) < range_bars + 1:
        reasons.append(f"{strategy}:post_event_range_forming")
        return None
    range_high = max(c.high for c in post[:range_bars])
    range_low = min(c.low for c in post[:range_bars])
    atr_value = atr(candles_m15, 14)
    if atr_value <= 0:
        reasons.append(f"{strategy}:atr_unavailable")
        return None
    if direction == "LONG":
        if quote.ask <= range_high:
            reasons.append(f"{strategy}:long_no_breakout")
            return None
        entry = quote.ask
        stop = entry - atr_value * 1.6
        target = entry + atr_value * 2.6
        distance = (entry - range_high) / atr_value
    else:
        if quote.bid >= range_low:
            reasons.append(f"{strategy}:short_no_breakout")
            return None
        entry = quote.bid
        stop = entry + atr_value * 1.6
        target = entry - atr_value * 2.6
        distance = (range_low - entry) / atr_value
    # Don't chase a break that already ran more than an ATR past the range.
    if distance > 1.0:
        reasons.append(f"{strategy}:breakout_too_extended")
        return None
    score = 80.0 + min(10.0, distance * 20.0)
    return Opportunity(
        symbol,
        instrument,
        direction,
        strategy,
        score,
        entry,
        stop,
        target,
        atr_value,
        1.6,
        0.85,
        f"Post-event range break ({window.title})",
        {"event": window.title, "range_high": range_high, "range_low": range_low, "spread_atr": quote.spread / atr_value},
    )
