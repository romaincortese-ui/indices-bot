from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from indicesbot.config import IndicesConfig
from indicesbot.indicators import atr, closes, trend
from indicesbot.models import Candle, MarketRegime
from indicesbot.risk_off import risk_off_aggressive_active


SESSION_ZONES = {
    "US": "America/New_York",
    "UK": "Europe/London",
    "EUROPE": "Europe/Berlin",
    "ASIA": "Asia/Tokyo",
    "ASIA_PACIFIC": "Australia/Sydney",
}


def session_name(region: str, now: datetime) -> str:
    zone = ZoneInfo(SESSION_ZONES.get(region, "UTC"))
    local = now.astimezone(zone)
    hour = local.hour
    if region == "US":
        if 9 <= hour < 11:
            return "US_CASH_OPEN"
        if 11 <= hour < 15:
            return "US_MIDDAY"
        if 15 <= hour < 16:
            return "US_FINAL_HOUR"
    if region in {"UK", "EUROPE"}:
        if 8 <= hour < 10:
            return "EUROPE_CASH_OPEN"
        if 10 <= hour < 16:
            return "EUROPE_ACTIVE"
    if region in {"ASIA", "ASIA_PACIFIC"}:
        if 9 <= hour < 11:
            return "ASIA_CASH_OPEN"
        if 11 <= hour < 15:
            return "ASIA_ACTIVE"
    return "OFF_HOURS"


def classify_regime(config: IndicesConfig, symbol: str, candles_h1: list[Candle], candles_h4: list[Candle], macro_state: dict, now: datetime) -> MarketRegime:
    region = config.region_for(symbol)
    trend_h4 = trend(candles_h4 or candles_h1, 20, 50)
    atr_value = atr(candles_h1, 14)
    price = closes(candles_h1)[-1] if candles_h1 else 0.0
    atr_pct = atr_value / price if price > 0 else 0.0
    if atr_pct >= 0.035:
        volatility = "EXTREME"
    elif atr_pct >= 0.02:
        volatility = "HIGH"
    elif atr_pct <= 0.006:
        volatility = "LOW"
    else:
        volatility = "NORMAL"
    aggressive_risk_off = risk_off_aggressive_active(config, macro_state)
    risk_mode = str((macro_state.get("risk_regime") or {}).get("global") or "MIXED").upper()
    if aggressive_risk_off:
        risk_mode = "RISK_OFF"
    blockers: list[str] = []
    risk_multiplier = 1.0
    score_long = 0.0
    score_short = 0.0
    if volatility == "EXTREME":
        blockers.append("volatility_extreme")
        risk_multiplier = 0.25
    elif volatility == "HIGH":
        risk_multiplier = 0.5
    if config.risk_off_filter_enabled and risk_mode == "RISK_OFF":
        score_short += 5.0
        score_long -= 6.0
        risk_multiplier = min(risk_multiplier, 0.75)
    elif risk_mode == "RISK_ON":
        score_long += 4.0
        score_short -= 3.0
    if aggressive_risk_off:
        score_short += config.risk_off_aggressive_short_score_bonus
        score_long -= config.risk_off_aggressive_long_score_penalty
        risk_multiplier *= max(0.0, config.risk_off_aggressive_risk_multiplier)
    return MarketRegime(symbol, region, trend_h4, volatility, risk_mode, session_name(region, now), score_long, score_short, risk_multiplier, tuple(blockers))
