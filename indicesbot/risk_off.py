from __future__ import annotations

from typing import Any

from indicesbot.config import IndicesConfig
from indicesbot.models import Opportunity


def risk_off_aggressive_active(config: IndicesConfig, macro_state: dict[str, Any]) -> bool:
    if not config.risk_off_aggressive_enabled:
        return False
    risk = _risk_payload(macro_state)
    risk_mode = str(risk.get("global") or "MIXED").upper()
    if risk_mode == "RISK_OFF":
        return True
    vix_level = _float_or_none(risk.get("vix")) or _float_or_none(risk.get("vix_level")) or _float_or_none(risk.get("vix_close"))
    vix_change_pct = _float_or_none(risk.get("vix_change_pct")) or 0.0
    if vix_level is not None and vix_level >= config.risk_off_aggressive_vix_level:
        return True
    return vix_change_pct >= config.risk_off_aggressive_vix_change_pct


def opportunity_min_score(config: IndicesConfig, opportunity: Opportunity, macro_state: dict[str, Any], *, default_min_score: float | None = None) -> float:
    base_score = config.min_score if default_min_score is None else default_min_score
    if risk_off_aggressive_active(config, macro_state) and opportunity.direction.upper() == "SHORT":
        return min(base_score, config.risk_off_aggressive_min_score)
    return base_score


def macro_strategy_allowed(config: IndicesConfig, opportunity: Opportunity, macro_state: dict[str, Any]) -> bool:
    return risk_on_strategy_allowed(config, opportunity, macro_state) and risk_off_strategy_allowed(config, opportunity, macro_state)


def risk_on_strategy_allowed(config: IndicesConfig, opportunity: Opportunity, macro_state: dict[str, Any]) -> bool:
    risk_mode = str(_risk_payload(macro_state).get("global") or "MIXED").upper()
    if risk_mode != "RISK_ON":
        return True
    allowed = {name.upper() for name in config.risk_on_enabled_strategies}
    return not allowed or opportunity.strategy.upper() in allowed


def risk_off_strategy_allowed(config: IndicesConfig, opportunity: Opportunity, macro_state: dict[str, Any]) -> bool:
    if not risk_off_aggressive_active(config, macro_state):
        return True
    allowed = {name.upper() for name in config.risk_off_aggressive_enabled_strategies}
    return not allowed or opportunity.strategy.upper() in allowed


def spread_cap_atr(config: IndicesConfig, macro_state: dict[str, Any]) -> float:
    if risk_off_aggressive_active(config, macro_state):
        return max(config.max_entry_spread_atr, config.risk_off_aggressive_max_entry_spread_atr)
    return config.max_entry_spread_atr


def profit_lock_thresholds(config: IndicesConfig, metadata: dict[str, Any]) -> tuple[float, float]:
    if metadata.get("risk_off_aggressive"):
        return config.risk_off_aggressive_profit_lock_trigger_pct, config.risk_off_aggressive_profit_lock_pullback_pct
    return config.profit_lock_trigger_pct, config.profit_lock_pullback_pct


def _risk_payload(macro_state: dict[str, Any]) -> dict[str, Any]:
    risk = macro_state.get("risk_regime") if isinstance(macro_state.get("risk_regime"), dict) else {}
    return risk if isinstance(risk, dict) else {}


def _float_or_none(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None