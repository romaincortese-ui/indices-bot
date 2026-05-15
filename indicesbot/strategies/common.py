from __future__ import annotations

from dataclasses import replace

from indicesbot.config import IndicesConfig
from indicesbot.models import Candle, IndexQuote, MarketRegime, Opportunity
from indicesbot.strategies.event_momentum import score_event_momentum
from indicesbot.strategies.mean_reversion import score_mean_reversion
from indicesbot.strategies.opening_range_breakout import score_opening_range_breakout
from indicesbot.strategies.trend_pullback import score_trend_pullback


def apply_regime(opportunity: Opportunity, regime: MarketRegime) -> Opportunity:
    offset = regime.score_offset_long if opportunity.direction == "LONG" else regime.score_offset_short
    return replace(opportunity, score=opportunity.score + offset, risk_multiplier=opportunity.risk_multiplier * regime.risk_multiplier)


def evaluate_all(config: IndicesConfig, symbol: str, instrument: str, quote: IndexQuote, candles_m15: list[Candle], candles_h1: list[Candle], candles_h4: list[Candle], regime: MarketRegime, macro_state: dict, reasons: list[str]) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    enabled = {strategy.upper() for strategy in getattr(config, "enabled_strategies", ())}
    scorers = (
        ("OPENING_RANGE_BREAKOUT", score_opening_range_breakout),
        ("TREND_PULLBACK", score_trend_pullback),
        ("MEAN_REVERSION", score_mean_reversion),
        ("EVENT_MOMENTUM", score_event_momentum),
    )
    for strategy_name, scorer in scorers:
        if enabled and strategy_name not in enabled:
            reasons.append(f"{strategy_name}:disabled")
            continue
        for direction in ("LONG", "SHORT"):
            opportunity = scorer(config, symbol, instrument, direction, quote, candles_m15, candles_h1, candles_h4, regime, macro_state, reasons)
            if opportunity is not None:
                opportunities.append(apply_regime(opportunity, regime))
    return opportunities


def select_best_opportunity(opportunities: list[Opportunity], *, min_score: float = 70.0) -> Opportunity | None:
    tradable = [item for item in opportunities if item.score >= min_score]
    if not tradable:
        return None
    return sorted(tradable, key=lambda item: (item.score, item.risk_reward, -item.metadata.get("spread_atr", 99.0)), reverse=True)[0]