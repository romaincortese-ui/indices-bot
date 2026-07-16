from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from indicesbot.config import IndicesConfig
from indicesbot.models import Candle, IndexQuote, MarketRegime, Opportunity
from indicesbot.strategies.event_momentum import score_event_momentum
from indicesbot.strategies.event_window import score_event_window
from indicesbot.strategies.mean_reversion import score_mean_reversion
from indicesbot.strategies.opening_range_breakout import score_opening_range_breakout
from indicesbot.strategies.trend_pullback import score_trend_pullback
from indicesbot.trading_calendar import opening_range_calendar_block


def apply_regime(opportunity: Opportunity, regime: MarketRegime) -> Opportunity:
    offset = regime.score_offset_long if opportunity.direction == "LONG" else regime.score_offset_short
    return replace(opportunity, score=opportunity.score + offset, risk_multiplier=opportunity.risk_multiplier * regime.risk_multiplier)


def strategy_lane_disabled(config: IndicesConfig, symbol: str, strategy: str, direction: str) -> bool:
    disabled = {str(item).strip().upper() for item in getattr(config, "disabled_strategy_lanes", ()) if str(item).strip()}
    if not disabled:
        return False
    symbol = symbol.upper()
    strategy = strategy.upper()
    direction = direction.upper()
    candidates = {
        f"{symbol}:{strategy}:{direction}",
        f"{symbol}:{strategy}:*",
        f"{symbol}:*:{direction}",
        f"*:{strategy}:{direction}",
        f"{symbol}:*:*",
        f"*:{strategy}:*",
        f"*:*:{direction}",
        "*:*:*",
    }
    return bool(disabled & candidates)


def evaluate_all(config: IndicesConfig, symbol: str, instrument: str, quote: IndexQuote, candles_m15: list[Candle], candles_h1: list[Candle], candles_h4: list[Candle], regime: MarketRegime, macro_state: dict, reasons: list[str]) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    enabled = {strategy.upper() for strategy in getattr(config, "enabled_strategies", ())}
    scorers = (
        ("OPENING_RANGE_BREAKOUT", score_opening_range_breakout),
        ("TREND_PULLBACK", score_trend_pullback),
        ("MEAN_REVERSION", score_mean_reversion),
        ("EVENT_MOMENTUM", score_event_momentum),
        ("EVENT_WINDOW", score_event_window),
    )
    for strategy_name, scorer in scorers:
        if enabled and strategy_name not in enabled:
            reasons.append(f"{strategy_name}:disabled")
            continue
        calendar_block = opening_range_calendar_block(
            symbol,
            strategy_name,
            quote.time,
            enabled=getattr(config, "us_holiday_orb_block_enabled", True),
        )
        if calendar_block:
            reasons.append(f"{strategy_name}:{calendar_block}")
            continue
        for direction in ("LONG", "SHORT"):
            if strategy_lane_disabled(config, symbol, strategy_name, direction):
                reasons.append(f"{strategy_name}:{direction.lower()}_lane_disabled")
                continue
            opportunity = scorer(config, symbol, instrument, direction, quote, candles_m15, candles_h1, candles_h4, regime, macro_state, reasons)
            if opportunity is not None and not _net_rr_ok(config, opportunity, quote.spread):
                reasons.append(f"{strategy_name}:net_rr_below_floor")
                opportunity = None
            if opportunity is not None:
                opportunities.append(apply_regime(opportunity, regime))
    return opportunities


def _net_rr_ok(config: IndicesConfig, opportunity: Opportunity, spread: float) -> bool:
    """Reward net of the spread must be >= min_net_rr x risk. At a ~33% win rate
    the winners must outsize the losers or the strategy drip-loses; the spread on
    M15 index scalps is large enough to invert a nominal 1.7 RR."""
    min_rr = max(0.0, getattr(config, "min_net_rr", 0.0))
    if min_rr <= 0 or opportunity.take_profit_price is None:
        return True
    risk = abs(opportunity.entry_price - opportunity.stop_price)
    reward = abs(opportunity.take_profit_price - opportunity.entry_price) - max(spread, 0.0)
    return risk > 0 and reward / risk >= min_rr


def select_best_opportunities(opportunities: list[Opportunity], *, min_score: float = 70.0, limit: int | None = None, score_threshold: Callable[[Opportunity], float] | None = None) -> list[Opportunity]:
    tradable = []
    for item in opportunities:
        threshold = score_threshold(item) if score_threshold is not None else min_score
        if item.score >= threshold:
            tradable.append(item)
    if not tradable:
        return []
    ranked = sorted(tradable, key=lambda item: (item.score, item.risk_reward, -item.metadata.get("spread_atr", 99.0)), reverse=True)
    return ranked if limit is None else ranked[:limit]


def select_best_opportunity(opportunities: list[Opportunity], *, min_score: float = 70.0, score_threshold: Callable[[Opportunity], float] | None = None) -> Opportunity | None:
    selected = select_best_opportunities(opportunities, min_score=min_score, limit=1, score_threshold=score_threshold)
    return selected[0] if selected else None