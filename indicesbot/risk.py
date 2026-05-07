from __future__ import annotations

from datetime import datetime, timezone
from math import floor
from typing import Any

from indicesbot.config import IndicesConfig
from indicesbot.models import AccountSummary, IndexPosition, InstrumentDetails, Opportunity


def position_from_opportunity(opportunity: Opportunity, config: IndicesConfig, account: AccountSummary, details: InstrumentDetails, conversion_factor: float) -> IndexPosition:
    stop_distance = abs(opportunity.entry_price - opportunity.stop_price)
    nav = max(account.nav, account.balance, config.paper_balance)
    risk_amount = nav * config.budget_allocation * config.max_risk_per_trade * opportunity.risk_multiplier
    per_unit_risk = max(stop_distance * max(conversion_factor, 0.0001), 0.0001)
    units = floor(risk_amount / per_unit_risk)
    margin_per_unit = opportunity.entry_price * max(details.margin_rate, 0.0001) * max(conversion_factor, 0.0001)
    max_units_by_margin = floor(max(0.0, account.margin_available * config.max_margin_per_entry_pct) / max(margin_per_unit, 0.0001))
    units = max(0, min(units, max_units_by_margin))
    signed_units = units if opportunity.direction == "LONG" else -units
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
        metadata={"score": opportunity.score, "risk_amount": risk_amount, **opportunity.metadata},
    )


def can_open(position: IndexPosition, state: dict[str, Any], config: IndicesConfig) -> tuple[bool, str]:
    if position.units < 1:
        return False, "units_below_minimum"
    rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
    if len(rows) >= config.max_open_indices_trades:
        return False, "max_open_indices_trades"
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
