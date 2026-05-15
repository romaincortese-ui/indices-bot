from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from indicesbot.calibration import calibration_quality, load_calibration, strategy_adjustment
from indicesbot.config import IndicesConfig, load_config
from indicesbot.daily_review import write_daily_review
from indicesbot.news import high_impact_event_block, load_macro_state, parse_events
from indicesbot.oanda_client import OandaClient
from indicesbot.regimes import classify_regime
from indicesbot.risk import can_open, fill_price_from_response, order_id_from_response, position_from_opportunity, position_to_row
from indicesbot.spread_tracker import SpreadTracker
from indicesbot.state import StateStore
from indicesbot.strategies import evaluate_all, select_best_opportunity
from indicesbot.telegram import TelegramClient, help_message, order_opened_message, order_rejected_message, opportunity_message, profit_lock_message, startup_message, status_message, trade_closed_message


log = logging.getLogger(__name__)


class IndicesRuntime:
    def __init__(self, config: IndicesConfig | None = None, client: OandaClient | None = None, telegram: TelegramClient | None = None) -> None:
        self.config = config or load_config()
        self.client = client or OandaClient(self.config)
        self.state_store = StateStore(path=self.config.state_file, redis_url=self.config.redis_url, redis_key=self.config.runtime_state_key)
        self.telegram = telegram or TelegramClient(token=self.config.telegram_token, chat_id=self.config.telegram_chat_id, offset_file=self.config.telegram_offset_file)
        self.spreads = SpreadTracker(window_minutes=self.config.adaptive_spread_window_minutes, multiplier=self.config.adaptive_spread_multiplier, min_samples=self.config.adaptive_spread_min_samples)

    def run_forever(self) -> None:
        state = self.state_store.load()
        state.setdefault("open_positions", [])
        state.setdefault("events", [])
        self.state_store.save(state)
        self._announce_startup(state)
        while True:
            self.run_cycle()
            if self.config.run_once:
                return
            time.sleep(self.config.scan_interval_seconds)

    def _announce_startup(self, state: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        key = self._startup_message_key()
        last_key = str(state.get("last_startup_telegram_key") or "")
        last_at = _parse_datetime(state.get("last_startup_telegram_at"))
        cooldown = timedelta(minutes=max(0, self.config.startup_message_cooldown_minutes))
        if cooldown.total_seconds() > 0 and last_key == key and last_at is not None and now - last_at < cooldown:
            log.info("startup_telegram_suppressed cooldown_minutes=%s", self.config.startup_message_cooldown_minutes)
            return
        self.telegram.send(startup_message(
            mode=self.config.execution_mode,
            universe=self.config.universe,
            account_label=self.config.oanda_env,
            calibration_required=self.config.require_calibration_for_trading,
        ))
        state["last_startup_telegram_at"] = now.isoformat()
        state["last_startup_telegram_key"] = key
        self.state_store.save(state)

    def _startup_message_key(self) -> str:
        deployment = os.getenv("RAILWAY_DEPLOYMENT_ID", "").strip() or os.getenv("RAILWAY_GIT_COMMIT_SHA", "").strip()
        return "|".join([
            deployment,
            self.config.execution_mode,
            self.config.oanda_env,
            ",".join(self.config.universe),
            ",".join(self.config.enabled_strategies),
            str(self.config.require_calibration_for_trading),
        ])

    def run_cycle(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        state = self.state_store.load()
        self._service_telegram(state)
        closed = self._sync_closed_positions(state)
        for row in closed:
            self.telegram.send(trade_closed_message(row, reason="not_in_oanda_open_positions"))
        time_stopped = self._apply_time_stop(state, now)
        for row in time_stopped:
            self.telegram.send(trade_closed_message(row, reason="max_hold_time_stop"))
        profit_updates, profit_errors = self._apply_profit_protection(state)
        for row in profit_updates:
            self.telegram.send(profit_lock_message(row))
        if profit_errors:
            state["last_profit_protection_errors"] = profit_errors[:5]
        if state.get("paused") or state.get("halted"):
            state["last_scan"] = {"status": "paused" if state.get("paused") else "halted", "blocked_by": "paused_or_halted", "at": now.isoformat()}
            self._save_status(state, "paused")
            return state

        macro_state = load_macro_state(self.config.macro_state_file)
        events = parse_events(macro_state)
        calibration = load_calibration(self.config.calibration_file)
        if self.config.require_calibration_for_trading and self.config.execution_mode in {"paper", "live"}:
            ready, reason = calibration_quality(
                calibration,
                min_trades=self.config.calibration_min_trades,
                min_groups=self.config.calibration_min_groups,
            )
            if not ready:
                state["last_scan"] = {"status": "blocked", "blocked_by": reason, "at": now.isoformat()}
                self._save_status(state, "blocked")
                return state

        account = self.client.account_summary()
        all_opportunities = []
        all_reasons: list[str] = []
        for symbol in self.config.universe:
            instrument = self.config.oanda_instrument_for(symbol)
            if not instrument:
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", "instrument_unavailable", 0.0)
                continue
            ok, tradeable_reason = self.client.instrument_tradeable(instrument)
            if not ok:
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", tradeable_reason, 0.0)
                continue
            quote = self.client.current_quote(symbol, instrument)
            candles_m15 = self.client.candles(instrument, count=120, granularity="M15")
            candles_h1 = self.client.candles(instrument, count=120, granularity="H1")
            candles_h4 = self.client.candles(instrument, count=120, granularity="H4") or candles_h1
            if not candles_m15 or not candles_h1:
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", "candles_unavailable", 0.0)
                continue
            regime = classify_regime(self.config, symbol, candles_h1, candles_h4, macro_state, now)
            event_block = high_impact_event_block(regime.region, events, now, pre_minutes=self.config.pre_event_pause_minutes, post_minutes=self.config.post_event_settle_minutes)
            if event_block:
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", "event_pause", 0.0)
                continue
            atr_value = max(all_candle_range(candles_m15), 0.0001)
            self.spreads.add(symbol, quote.spread, now=now)
            spread_decision = self.spreads.evaluate(symbol, quote.spread, static_cap=atr_value * self.config.max_entry_spread_atr, atr_cap=atr_value * self.config.max_entry_spread_atr, now=now)
            if not spread_decision.ok:
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", spread_decision.reason, 0.0)
                continue
            reasons: list[str] = []
            opportunities = evaluate_all(self.config, symbol, instrument, quote, candles_m15, candles_h1, candles_h4, regime, macro_state, reasons)
            adjusted = []
            for opportunity in opportunities:
                score_offset, risk_multiplier = strategy_adjustment(calibration, symbol=symbol, strategy=opportunity.strategy, direction=opportunity.direction)
                adjusted.append(replace(opportunity, score=opportunity.score + score_offset, risk_multiplier=opportunity.risk_multiplier * risk_multiplier))
            all_opportunities.extend(adjusted)
            all_reasons.extend([f"{symbol}:{reason}" for reason in reasons])
        state["signals_seen"] = int(state.get("signals_seen", 0) or 0) + len(all_opportunities)
        best = select_best_opportunity(all_opportunities, min_score=self.config.min_score)
        if best is None:
            state["last_scan"] = {"status": "idle", "blocked_by": "score_below_threshold", "reasons": all_reasons[-25:], "at": now.isoformat()}
            self._save_status(state, "idle")
            return state
        if self.config.execution_mode == "signal_only":
            state["last_scan"] = {"status": "signal_only", "best_opportunity": best.symbol, "at": now.isoformat()}
            self.telegram.send(opportunity_message(best, mode="signal_only"))
            self._save_status(state, "signal_only")
            return state
        details = self.client.instrument_details(best.instrument)
        position = position_from_opportunity(best, self.config, account, details, self.client.home_conversion_factor(best.instrument, best.direction))
        can_trade, reason = can_open(position, state, self.config)
        if not can_trade:
            self._record_miss(state, best.symbol, best.direction, best.strategy, reason, best.score)
            state["last_scan"] = {"status": "blocked", "blocked_by": reason, "best_opportunity": best.symbol, "at": now.isoformat()}
            self._save_status(state, "blocked")
            return state
        try:
            response = self.client.place_market_order(best, position.order_units)
            order_id = order_id_from_response(response)
            if not order_id:
                raise RuntimeError("order_not_filled")
            fill_price = fill_price_from_response(response)
            if fill_price:
                position.entry_price = fill_price
            position.order_id = order_id
            row = position_to_row(position)
            state.setdefault("open_positions", []).append(row)
            state.setdefault("events", []).append({"type": "trade_opened", "symbol": best.symbol, "direction": best.direction, "strategy": best.strategy, "order_id": order_id, "at": now.isoformat()})
            state["last_scan"] = {"status": "trade_opened", "symbol": best.symbol, "direction": best.direction, "strategy": best.strategy, "at": now.isoformat()}
            self.telegram.send(order_opened_message(position))
            log.info("trade_opened symbol=%s direction=%s strategy=%s units=%.2f order_id=%s", best.symbol, best.direction, best.strategy, position.units, order_id)
        except Exception as exc:
            reason = str(exc)
            self._record_miss(state, best.symbol, best.direction, best.strategy, reason, best.score)
            self.telegram.send(order_rejected_message(symbol=best.symbol, direction=best.direction, strategy=best.strategy, reason=reason))
            state["last_scan"] = {"status": "order_rejected", "blocked_by": reason, "symbol": best.symbol, "at": now.isoformat()}
            log.info("order_not_filled symbol=%s direction=%s strategy=%s blocked_by=%s", best.symbol, best.direction, best.strategy, reason)
        self._save_status(state, "running")
        return state

    def _sync_closed_positions(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
        if self.config.paper_trade or not self.config.has_oanda_credentials:
            return []
        open_instruments = self.client.open_positions()
        kept = [row for row in rows if str(row.get("instrument", "")).upper() in open_instruments]
        closed = [row for row in rows if row not in kept]
        state["open_positions"] = kept
        for row in closed:
            state.setdefault("events", []).append({"type": "trade_closed", "symbol": row.get("symbol"), "order_id": row.get("order_id"), "at": datetime.now(timezone.utc).isoformat()})
        return closed

    def _apply_time_stop(self, state: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
        max_bars = max(0, int(self.config.max_hold_bars))
        bar_minutes = max(1, int(self.config.bar_minutes))
        if max_bars <= 0:
            return []
        max_age = timedelta(minutes=max_bars * bar_minutes)
        rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
        if not rows:
            return []
        broker_available = not self.config.paper_trade and self.config.live_trading_enabled and self.config.has_oanda_credentials
        kept: list[dict[str, Any]] = []
        stopped: list[dict[str, Any]] = []
        for row in rows:
            opened_at_raw = row.get("opened_at")
            try:
                opened_at = datetime.fromisoformat(str(opened_at_raw)) if opened_at_raw else None
            except ValueError:
                opened_at = None
            if opened_at is None:
                kept.append(row)
                continue
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            if (now - opened_at) < max_age:
                kept.append(row)
                continue
            trade_id = str(row.get("order_id") or "")
            if broker_available and trade_id:
                try:
                    self.client.close_trade(trade_id)
                except RuntimeError as exc:
                    log.warning("time_stop_close_failed instrument=%s order_id=%s error=%s", row.get("instrument"), trade_id, exc)
                    kept.append(row)
                    continue
            stopped.append(row)
            state.setdefault("events", []).append({
                "type": "trade_closed",
                "reason": "max_hold_time_stop",
                "symbol": row.get("symbol"),
                "instrument": row.get("instrument"),
                "order_id": trade_id,
                "age_minutes": int((now - opened_at).total_seconds() // 60),
                "at": now.isoformat(),
            })
        state["open_positions"] = kept
        return stopped

    def _apply_profit_protection(self, state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        updates: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        if not self.config.profit_lock_enabled:
            state["last_profit_protection_updates"] = []
            return updates, errors
        broker_available = not self.config.paper_trade and self.config.live_trading_enabled and self.config.has_oanda_credentials
        rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
        if not rows:
            state["last_profit_protection_updates"] = []
            return updates, errors
        trades: list[dict[str, object]] = []
        if broker_available:
            try:
                trades = self.client.open_trades()
            except RuntimeError as exc:
                errors.append({"stage": "profit_lock_open_trades", "error": str(exc)})
                state["last_profit_protection_errors"] = errors[:5]
                return updates, errors
        trades_by_id = {str(trade.get("id") or ""): trade for trade in trades if isinstance(trade, dict) and trade.get("id")}
        trades_by_instrument: dict[str, dict[str, object]] = {}
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            instrument = str(trade.get("instrument") or "").strip().upper()
            if instrument and instrument not in trades_by_instrument:
                trades_by_instrument[instrument] = trade
        kept_rows: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for row in rows:
            if not isinstance(row, dict):
                continue
            instrument = str(row.get("instrument") or "").strip().upper()
            trade_id = str(row.get("order_id") or "").strip()
            trade = trades_by_id.get(trade_id) or trades_by_instrument.get(instrument)
            if not instrument or not trade_id or not isinstance(trade, dict):
                kept_rows.append(row)
                continue
            self._enrich_row_from_trade(row, trade)
            pnl_pct = _position_pnl_pct(row)
            if pnl_pct is None:
                kept_rows.append(row)
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            metadata = dict(metadata)
            peak_pnl_pct = _float_or_none(metadata.get("peak_pnl_pct"))
            if peak_pnl_pct is None or pnl_pct > peak_pnl_pct:
                peak_pnl_pct = pnl_pct
                metadata["peak_pnl_pct"] = peak_pnl_pct
                metadata["peak_seen_at"] = now.isoformat()
                metadata["peak_current_value"] = _position_current_value(row)
                metadata["peak_unrealized_pl"] = _position_unrealized_pl(row)
                row["metadata"] = metadata
            pullback_pct = peak_pnl_pct - pnl_pct
            if peak_pnl_pct < max(0.0, self.config.profit_lock_trigger_pct) or pullback_pct < max(0.0, self.config.profit_lock_pullback_pct) or pnl_pct <= 0.0:
                kept_rows.append(row)
                continue
            if broker_available:
                try:
                    self.client.close_trade(trade_id)
                except RuntimeError as exc:
                    errors.append({"symbol": row.get("symbol") or instrument, "instrument": instrument, "stage": "profit_lock_close", "error": str(exc)})
                    kept_rows.append(row)
                    continue
            row["pnl_pct"] = pnl_pct
            row["peak_pnl_pct"] = peak_pnl_pct
            row["pullback_from_peak_pct"] = pullback_pct
            row["closed_at"] = now.isoformat()
            row["exit_reason"] = "peak_pullback_profit_lock"
            updates.append(dict(row))
            state.setdefault("events", []).append({"type": "profit_lock_closed", "symbol": row.get("symbol"), "order_id": trade_id, "peak_pnl_pct": peak_pnl_pct, "pnl_pct": pnl_pct, "at": now.isoformat()})
        state["open_positions"] = kept_rows
        state["last_profit_protection_updates"] = updates
        state["last_profit_protection_errors"] = errors[:5]
        return updates, errors

    def _enrich_row_from_trade(self, row: dict[str, Any], trade: dict[str, object]) -> None:
        entry_budget = _float_or_none(trade.get("initialMarginRequired"))
        if entry_budget is None or entry_budget <= 0:
            entry_budget = _float_or_none(trade.get("marginUsed"))
        if entry_budget is not None and entry_budget > 0:
            row["entry_budget"] = entry_budget
            row["initial_margin_required"] = _float_or_none(trade.get("initialMarginRequired"))
            row["margin_used"] = _float_or_none(trade.get("marginUsed"))
        unrealized_pl = _float_or_none(trade.get("unrealizedPL"))
        if unrealized_pl is not None:
            row["unrealized_pl"] = unrealized_pl
        if entry_budget is not None and unrealized_pl is not None:
            row["current_value"] = entry_budget + unrealized_pl
        price = _float_or_none(trade.get("price"))
        if price is not None and price > 0:
            row["entry_price"] = price
        units = _float_or_none(trade.get("currentUnits"))
        if units is not None and units != 0.0:
            row["order_units"] = units
            row["units"] = abs(units)
            row["direction"] = "LONG" if units > 0 else "SHORT"

    def _service_telegram(self, state: dict[str, Any]) -> None:
        if not self.telegram.enabled:
            return
        offset = self.telegram.load_offset()
        for update in self.telegram.get_updates(offset, timeout=1):
            update_id = int(update.get("update_id", offset))
            offset = max(offset, update_id + 1)
            message = update.get("message", {}) if isinstance(update.get("message"), dict) else {}
            text = str(message.get("text") or "").strip().lower()
            if text == "/status":
                self.telegram.send(status_message(state))
            elif text == "/open":
                self.telegram.send(_open_positions_message(state))
            elif text == "/events":
                self.telegram.send(_events_message(load_macro_state(self.config.macro_state_file)))
            elif text == "/pause":
                state["paused"] = True
                self.telegram.send("Indices Bot paused. New entries are paused; existing positions remain monitored.")
            elif text == "/resume":
                state["paused"] = False
                self.telegram.send("Indices Bot resumed. New entries are allowed again on the next scan.")
            elif text == "/sync":
                state["sync_requested"] = True
                self.telegram.send("Broker sync request recorded. Closed-position sync also runs automatically each cycle.")
            elif text == "/closeall":
                state["close_all_requested"] = True
                self.telegram.send("Close-all request recorded for operator follow-up. This runtime does not automatically close trades yet.")
            elif text in {"/help", "help"}:
                self.telegram.send(help_message())
        self.telegram.save_offset(offset)

    def _record_miss(self, state: dict[str, Any], symbol: str, direction: str, strategy: str, reason: str, score: float) -> None:
        state.setdefault("missed_opportunities", []).append({"symbol": symbol, "direction": direction, "strategy": strategy, "blocked_by": reason, "score": score, "at": datetime.now(timezone.utc).isoformat()})

    def _save_status(self, state: dict[str, Any], status: str) -> None:
        self.state_store.save(state)
        write_daily_review(self.config.daily_review_file, state)
        self.state_store.publish_status(self.config.bot_status_key, {"status": status, "updated_at": datetime.now(timezone.utc).isoformat(), "last_scan": state.get("last_scan", {})})


def all_candle_range(candles: list[Any]) -> float:
    values = [max(0.0, candle.high - candle.low) for candle in candles[-14:]]
    return sum(values) / len(values) if values else 0.0


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _position_entry_budget(row: dict[str, Any]) -> float | None:
    for key in ("entry_budget", "initial_margin_required", "margin_used"):
        value = _float_or_none(row.get(key))
        if value is not None and value > 0:
            return value
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    value = _float_or_none(metadata.get("risk_amount"))
    return value if value is not None and value > 0 else None


def _position_unrealized_pl(row: dict[str, Any]) -> float | None:
    for key in ("unrealized_pl", "unrealizedPL"):
        value = _float_or_none(row.get(key))
        if value is not None:
            return value
    entry_budget = _position_entry_budget(row)
    current_value = _float_or_none(row.get("current_value"))
    if entry_budget is not None and current_value is not None:
        return current_value - entry_budget
    return None


def _position_current_value(row: dict[str, Any]) -> float | None:
    current_value = _float_or_none(row.get("current_value"))
    if current_value is not None:
        return current_value
    entry_budget = _position_entry_budget(row)
    unrealized_pl = _position_unrealized_pl(row)
    if entry_budget is not None and unrealized_pl is not None:
        return entry_budget + unrealized_pl
    return None


def _position_pnl_pct(row: dict[str, Any]) -> float | None:
    entry_budget = _position_entry_budget(row)
    unrealized_pl = _position_unrealized_pl(row)
    if entry_budget is None or entry_budget <= 0 or unrealized_pl is None:
        return None
    return unrealized_pl / entry_budget * 100.0


def _open_positions_message(state: dict[str, Any]) -> str:
    rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
    if not rows:
        return "📂 <b>Open Index Positions</b>\n━━━━━━━━━━━━━━━\nNo open index positions are tracked right now."
    lines = ["📂 <b>Open Index Positions</b>", "━━━━━━━━━━━━━━━", "Tracked in runtime state."]
    for row in rows[:10]:
        if not isinstance(row, dict):
            continue
        target = row.get("take_profit_price") or "managed exit"
        direction_value = str(row.get("direction", "")).upper()
        if direction_value == "LONG":
            direction = "🟢 LONG"
        elif direction_value == "SHORT":
            direction = "🔴 SHORT"
        else:
            direction = "⚪ UNKNOWN"
        lines.append("")
        lines.append(f"{direction} <b>{escape(str(row.get('symbol', '?')))}</b> | {escape(str(row.get('strategy', '?')).replace('_', ' ').title())}")
        lines.append(f"Instrument: {escape(str(row.get('instrument', '?')))} | Units: {escape(str(row.get('units', '?')))} | Order ID: {escape(str(row.get('order_id', '?')))}")
        lines.append(f"Entry: {escape(str(row.get('entry_price', '?')))} | Stop: {escape(str(row.get('stop_price', '?')))} | Target: {escape(str(target))}")
    if len(rows) > 10:
        lines.append(f"+{len(rows) - 10} more")
    return "\n".join(lines)


def _events_message(macro_state: dict[str, Any]) -> str:
    events = macro_state.get("events", []) if isinstance(macro_state.get("events"), list) else []
    if not events:
        return "🗂️ <b>Cached Index Events</b>\n━━━━━━━━━━━━━━━\nNo high-impact index events are currently cached."
    lines = ["🗂️ <b>Cached Index Events</b>", "━━━━━━━━━━━━━━━"]
    for event in events[:8]:
        if isinstance(event, dict):
            lines.append(f"⚠️ {escape(str(event.get('region', 'GLOBAL')))} {escape(str(event.get('impact', '')))}: {escape(str(event.get('title', 'n/a')))}")
            lines.append(f"At: {escape(str(event.get('occurs_at', 'n/a')))}")
    return "\n".join(lines)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
