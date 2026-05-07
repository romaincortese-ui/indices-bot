from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from indicesbot.calibration import load_calibration, strategy_adjustment
from indicesbot.config import IndicesConfig, load_config
from indicesbot.daily_review import write_daily_review
from indicesbot.news import high_impact_event_block, load_macro_state, parse_events
from indicesbot.oanda_client import OandaClient
from indicesbot.regimes import classify_regime
from indicesbot.risk import can_open, fill_price_from_response, order_id_from_response, position_from_opportunity, position_to_row
from indicesbot.spread_tracker import SpreadTracker
from indicesbot.state import StateStore
from indicesbot.strategies import evaluate_all, select_best_opportunity
from indicesbot.telegram import TelegramClient, help_message, order_opened_message, order_rejected_message, opportunity_message, startup_message, status_message, trade_closed_message


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
        self.telegram.send(startup_message(mode=self.config.execution_mode, universe=self.config.universe, account_label=self.config.oanda_env))
        while True:
            self.run_cycle()
            if self.config.run_once:
                return
            time.sleep(self.config.scan_interval_seconds)

    def run_cycle(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        state = self.state_store.load()
        self._service_telegram(state)
        closed = self._sync_closed_positions(state)
        for row in closed:
            self.telegram.send(trade_closed_message(row, reason="not_in_oanda_open_positions"))
        if state.get("paused") or state.get("halted"):
            state["last_scan"] = {"status": "paused" if state.get("paused") else "halted", "blocked_by": "paused_or_halted", "at": now.isoformat()}
            self._save_status(state, "paused")
            return state

        account = self.client.account_summary()
        macro_state = load_macro_state(self.config.macro_state_file)
        events = parse_events(macro_state)
        calibration = load_calibration(self.config.calibration_file)
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
        best = select_best_opportunity(all_opportunities)
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
                self.telegram.send("Indices Bot paused. Existing positions will still be monitored.")
            elif text == "/resume":
                state["paused"] = False
                self.telegram.send("Indices Bot resumed. New entries are allowed again.")
            elif text == "/sync":
                state["sync_requested"] = True
                self.telegram.send("Sync requested. The runtime will refresh broker state on the next cycle.")
            elif text == "/closeall":
                state["close_all_requested"] = True
                self.telegram.send("Close-all requested. The runtime will process this on the next cycle.")
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


def _open_positions_message(state: dict[str, Any]) -> str:
    rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
    if not rows:
        return "No open indices positions."
    lines = ["Open indices positions"]
    for row in rows:
        lines.append(f"{row.get('symbol')} {row.get('direction')} {row.get('strategy')} units={row.get('units')} entry={row.get('entry_price')}")
    return "\n".join(lines)


def _events_message(macro_state: dict[str, Any]) -> str:
    events = macro_state.get("events", []) if isinstance(macro_state.get("events"), list) else []
    if not events:
        return "No high-impact index events currently cached."
    lines = ["Cached index events"]
    for event in events[:8]:
        if isinstance(event, dict):
            lines.append(f"{event.get('region', 'GLOBAL')} {event.get('impact', '')}: {event.get('title')} at {event.get('occurs_at')}")
    return "\n".join(lines)
