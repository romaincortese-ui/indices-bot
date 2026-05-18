from __future__ import annotations

from datetime import datetime, timezone
from math import floor
from typing import Any

from indicesbot.config import IndicesConfig
from indicesbot.models import AccountSummary, IndexPosition, InstrumentDetails, Opportunity


def position_from_opportunity(opportunity: Opportunity, config: IndicesConfig, account: AccountSummary, details: InstrumentDetails, conversion_factor: float) -> IndexPosition:
    stop_distance = abs(opportunity.entry_price - opportunity.stop_price)
    nav = max(account.nav, account.balance, 0.0001)
    target_risk_amount = nav * config.budget_allocation * config.max_risk_per_trade * opportunity.risk_multiplier
    per_unit_risk = max(stop_distance * max(conversion_factor, 0.0001), 0.0001)
    raw_units = target_risk_amount / per_unit_risk
    margin_per_unit = opportunity.entry_price * max(details.margin_rate, 0.0001) * max(conversion_factor, 0.0001)
    max_units_by_margin = max(0.0, account.margin_available * config.max_margin_per_entry_pct) / max(margin_per_unit, 0.0001)
    unit_step = _unit_step(details)
    minimum_trade_size = max(details.minimum_trade_size, unit_step)
    units = _floor_to_unit_step(max(0.0, min(raw_units, max_units_by_margin)), unit_step, details.trade_units_precision)
    minimum_unit_risk_nav_pct = minimum_trade_size * per_unit_risk / max(nav, 0.0001)
    target_risk_nav_pct = target_risk_amount / max(nav, 0.0001)
    minimum_unit_target_risk_multiple = minimum_unit_risk_nav_pct / max(target_risk_nav_pct, 0.0001)
    minimum_unit_margin_amount = minimum_trade_size * margin_per_unit
    minimum_unit_margin_nav_pct = minimum_unit_margin_amount / max(nav, 0.0001)
    min_unit_floor_applied = False
    min_unit_floor_reason = "not_needed" if units >= minimum_trade_size else "disabled"
    if units < minimum_trade_size and config.min_unit_floor_enabled:
        margin_ok = max_units_by_margin >= minimum_trade_size
        primary_risk_ok = minimum_unit_risk_nav_pct <= max(0.0, config.min_unit_floor_max_risk_nav_pct)
        small_account_margin_ok = minimum_unit_margin_nav_pct <= max(0.0, config.min_unit_floor_small_account_max_margin_nav_pct)
        small_account_risk_ok = (
            minimum_unit_risk_nav_pct <= max(0.0, config.min_unit_floor_small_account_max_risk_nav_pct)
            and minimum_unit_target_risk_multiple <= max(1.0, config.min_unit_floor_max_target_risk_multiple)
            and small_account_margin_ok
        )
        if not margin_ok:
            min_unit_floor_reason = "margin_required_above_available"
        elif primary_risk_ok:
            min_unit_floor_reason = "primary_risk_cap"
        elif small_account_risk_ok:
            min_unit_floor_reason = "small_account_risk_cap"
        elif not small_account_margin_ok:
            min_unit_floor_reason = "small_account_margin_cap_exceeded"
        else:
            min_unit_floor_reason = "risk_cap_exceeded"
        if margin_ok and (primary_risk_ok or small_account_risk_ok):
            units = _round_units(minimum_trade_size, details.trade_units_precision)
            min_unit_floor_applied = True
    signed_units = units if opportunity.direction == "LONG" else -units
    actual_risk_amount = units * per_unit_risk
    return IndexPosition(
        symbol=opportunity.symbol,
        instrument=opportunity.instrument,
        direction=opportunity.direction,
        strategy=opportunity.strategy,
        units=float(units),
        order_units=float(signed_units),
        entry_price=opportunity.entry_price,
        stop_price=opportunity.stop_price,
        take_profit_price=opportunity.take_profit_price,
        opened_at=datetime.now(timezone.utc),
        region=config.region_for(opportunity.symbol),
        order_id="pending",
        metadata={
            "score": opportunity.score,
            "risk_amount": actual_risk_amount,
            "risk_nav_pct": actual_risk_amount / max(nav, 0.0001),
            "target_risk_amount": target_risk_amount,
            "target_risk_nav_pct": target_risk_nav_pct,
            "nav_at_entry": nav,
            "raw_units": raw_units,
            "max_units_by_margin": max_units_by_margin,
            "minimum_trade_size": minimum_trade_size,
            "unit_step": unit_step,
            "minimum_unit_risk_nav_pct": minimum_unit_risk_nav_pct,
            "minimum_unit_target_risk_multiple": minimum_unit_target_risk_multiple,
            "minimum_unit_margin_amount": minimum_unit_margin_amount,
            "minimum_unit_margin_nav_pct": minimum_unit_margin_nav_pct,
            "min_unit_floor_applied": min_unit_floor_applied,
            "min_unit_floor_reason": min_unit_floor_reason,
            **opportunity.metadata,
        },
    )


def can_open(position: IndexPosition, state: dict[str, Any], config: IndicesConfig) -> tuple[bool, str]:
    if position.units <= 0:
        return False, "units_below_minimum"
    rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
    if len(rows) >= config.max_open_indices_trades:
        return False, "max_open_indices_trades"
    total_risk = sum(_risk_nav_pct(row, config) for row in rows)
    current_risk = _float_or_zero(position.metadata.get("risk_nav_pct"))
    total_risk += current_risk
    if total_risk > max(0.0, config.max_total_indices_risk):
        small_account_floor_allowed = (
            bool(position.metadata.get("min_unit_floor_applied"))
            and current_risk <= max(0.0, config.min_unit_floor_small_account_max_risk_nav_pct)
            and total_risk <= max(0.0, config.min_unit_floor_small_account_max_total_risk_nav_pct)
        )
        if not small_account_floor_allowed:
            return False, "max_total_indices_risk"
    same_symbol = [row for row in rows if str(row.get("symbol", "")).upper() == position.symbol.upper()]
    if len(same_symbol) >= config.max_open_per_symbol:
        return False, "max_open_per_symbol"
    same_region = [row for row in rows if str(row.get("region", "")).upper() == position.region.upper()]
    if len(same_region) >= config.max_open_per_region:
        return False, "max_open_per_region"
    if state.get("paused"):
        return False, "paused_manual"
    if state.get("halted"):
        return False, "risk_halt"
    return True, "ok"


def _risk_nav_pct(row: dict[str, Any], config: IndicesConfig) -> float:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    explicit = _float_or_zero(metadata.get("risk_nav_pct"))
    if explicit > 0:
        return explicit
    risk_amount = _float_or_zero(metadata.get("risk_amount"))
    nav_at_entry = _float_or_zero(metadata.get("nav_at_entry")) or config.paper_balance
    return risk_amount / max(nav_at_entry, 0.0001)


def _float_or_zero(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _unit_step(details: InstrumentDetails) -> float:
    precision_step = 10 ** -max(0, details.trade_units_precision)
    return max(precision_step, 0.00000001)


def _floor_to_unit_step(value: float, step: float, precision: int) -> float:
    if value <= 0:
        return 0.0
    return _round_units(floor((value + 1e-12) / step) * step, precision)


def _round_units(value: float, precision: int) -> float:
    return round(value, max(0, precision))


def position_to_row(position: IndexPosition) -> dict[str, Any]:
    return {
        "symbol": position.symbol,
        "instrument": position.instrument,
        "direction": position.direction,
        "strategy": position.strategy,
        "units": position.units,
        "order_units": position.order_units,
        "entry_price": position.entry_price,
        "stop_price": position.stop_price,
        "take_profit_price": position.take_profit_price,
        "opened_at": position.opened_at.isoformat(),
        "region": position.region,
        "order_id": position.order_id,
        "metadata": position.metadata,
    }


def order_id_from_response(response: dict[str, object]) -> str | None:
    fill = response.get("orderFillTransaction")
    if isinstance(fill, dict):
        opened = fill.get("tradeOpened")
        if isinstance(opened, dict) and opened.get("tradeID"):
            return str(opened["tradeID"])
        if fill.get("id"):
            return str(fill["id"])
    return None


def fill_price_from_response(response: dict[str, object]) -> float | None:
    fill = response.get("orderFillTransaction")
    if isinstance(fill, dict):
        try:
            return float(fill.get("price"))
        except (TypeError, ValueError):
            return None
    return None
