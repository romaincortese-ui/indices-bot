from __future__ import annotations

from typing import Any

from indicesbot.models import MarketRegime


CASH_OPEN_SESSIONS = {"US_CASH_OPEN", "EUROPE_CASH_OPEN", "ASIA_CASH_OPEN"}
ACTIVE_SESSIONS = {
    "US_CASH_OPEN",
    "US_MIDDAY",
    "US_FINAL_HOUR",
    "EUROPE_CASH_OPEN",
    "EUROPE_ACTIVE",
    "ASIA_CASH_OPEN",
    "ASIA_ACTIVE",
}
TREND_SESSIONS = ACTIVE_SESSIONS
MEAN_REVERSION_SESSIONS = {"US_MIDDAY", "US_FINAL_HOUR", "EUROPE_ACTIVE", "ASIA_ACTIVE"}
HIGH_BETA_INDICES = {"NAS100", "DE40", "HK33"}
STRUCTURAL_LONG_BIAS = {"SPX500", "NAS100", "US30", "DE40", "JP225", "AU200"}


def is_cash_open(regime: MarketRegime) -> bool:
    return regime.session in CASH_OPEN_SESSIONS


def is_active_session(regime: MarketRegime) -> bool:
    return regime.session in ACTIVE_SESSIONS


def session_allows(strategy: str, regime: MarketRegime) -> bool:
    normalized = strategy.upper()
    if normalized == "OPENING_RANGE_BREAKOUT":
        return is_cash_open(regime)
    if normalized == "MEAN_REVERSION":
        return regime.session in MEAN_REVERSION_SESSIONS
    if normalized in {"TREND_PULLBACK", "EVENT_MOMENTUM"}:
        return regime.session in TREND_SESSIONS
    return is_active_session(regime)


def session_score_bonus(strategy: str, regime: MarketRegime) -> float:
    normalized = strategy.upper()
    if normalized == "OPENING_RANGE_BREAKOUT" and is_cash_open(regime):
        return 4.0
    if normalized == "TREND_PULLBACK" and regime.session in {"US_CASH_OPEN", "EUROPE_CASH_OPEN", "US_FINAL_HOUR"}:
        return 2.0
    if normalized == "MEAN_REVERSION" and regime.session in {"US_MIDDAY", "EUROPE_ACTIVE", "ASIA_ACTIVE"}:
        return 1.0
    if normalized == "EVENT_MOMENTUM" and is_active_session(regime):
        return 2.0
    return 0.0


def risk_allows_direction(symbol: str, direction: str, regime: MarketRegime, macro_state: dict[str, Any]) -> bool:
    normalized_symbol = symbol.upper()
    normalized_direction = direction.upper()
    if regime.risk_mode == "RISK_OFF" and normalized_direction == "LONG":
        return False
    if normalized_direction == "SHORT" and normalized_symbol in STRUCTURAL_LONG_BIAS and regime.risk_mode != "RISK_OFF" and not _has_strong_negative_event(regime, macro_state):
        return False
    if normalized_symbol in HIGH_BETA_INDICES and normalized_direction == "LONG" and vix_is_rising(macro_state):
        return False
    return True


def vix_level(macro_state: dict[str, Any]) -> float | None:
    risk = macro_state.get("risk_regime") if isinstance(macro_state.get("risk_regime"), dict) else {}
    for key in ("vix", "vix_level", "vix_close"):
        value = _float_or_none(risk.get(key))
        if value is not None:
            return value
    return None


def vix_change_pct(macro_state: dict[str, Any]) -> float:
    risk = macro_state.get("risk_regime") if isinstance(macro_state.get("risk_regime"), dict) else {}
    return _float_or_none(risk.get("vix_change_pct")) or 0.0


def vix_is_rising(macro_state: dict[str, Any]) -> bool:
    level = vix_level(macro_state)
    return vix_change_pct(macro_state) >= 8.0 or (level is not None and level >= 25.0)


def vix_is_extreme(macro_state: dict[str, Any]) -> bool:
    level = vix_level(macro_state)
    return vix_change_pct(macro_state) >= 18.0 or (level is not None and level >= 35.0)


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _has_strong_negative_event(regime: MarketRegime, macro_state: dict[str, Any]) -> bool:
    scores = macro_state.get("event_scores", []) if isinstance(macro_state.get("event_scores"), list) else []
    for row in scores:
        if not isinstance(row, dict):
            continue
        region = str(row.get("region") or "").upper()
        if region not in {regime.region, "GLOBAL"}:
            continue
        score = _float_or_none(row.get("score")) or 0.0
        if score <= -0.45:
            return True
    return False
