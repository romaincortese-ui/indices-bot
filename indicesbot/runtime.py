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
from indicesbot.news import default_macro_state, high_impact_event_block, load_macro_state, parse_events
from indicesbot.oanda_client import OandaClient
from indicesbot.prediction_overlay import apply_prediction_overlay, load_prediction_state_payload, select_point_in_time_prediction_state
from indicesbot.regimes import classify_regime
from indicesbot.risk_off import macro_strategy_allowed, opportunity_min_score, profit_lock_thresholds, risk_off_aggressive_active, spread_cap_atr
from indicesbot.risk import can_open, fill_price_from_response, order_id_from_response, position_from_opportunity, position_to_row
from indicesbot.spread_tracker import SpreadTracker
from indicesbot.state import StateStore
from indicesbot.strategies import evaluate_all, select_best_opportunities
from indicesbot.telegram import TelegramClient, help_message, order_opened_message, order_rejected_message, opportunity_message, profit_lock_message, startup_message, status_message, trade_closed_message


log = logging.getLogger(__name__)


def _prediction_overlay_status(config: IndicesConfig, prediction_state: dict[str, Any] | None) -> str:
    if not config.prediction_overlay_enabled:
        return "disabled"
    state_file = str(config.prediction_overlay_state_file or "").strip()
    source = "state file configured" if state_file else "state file missing"
    loaded = "state loaded" if isinstance(prediction_state, dict) else "no state loaded"
    return f"enabled | {loaded} | {source} | fallback {config.prediction_overlay_fallback_mode}"


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
        log.info(
            "indices_bot_starting mode=%s oanda_env=%s universe=%d enabled_strategies=%s calibration_required=%s scan_interval_seconds=%d",
            self.config.execution_mode,
            self.config.oanda_env,
            len(self.config.universe),
            ",".join(self.config.enabled_strategies),
            self.config.require_calibration_for_trading,
            self.config.scan_interval_seconds,
        )
        self._log_startup_health()
        self._announce_startup(state)
        while True:
            self.run_cycle()
            if self.config.run_once:
                return
            time.sleep(self.config.scan_interval_seconds)

    def _log_startup_health(self) -> None:
        if not self.config.has_oanda_credentials:
            log.info("oanda_account_check skipped reason=credentials_missing")
            return
        # OANDA maintenance windows return transient errors; a hard failure
        # here crash-loops the container and the bot stays dead until a manual
        # redeploy (observed 2026-06-05..11: 6 days down). Retry with backoff,
        # then degrade to a warning instead of dying - the trading loop has
        # its own per-cycle error handling.
        account = None
        for attempt in range(1, 6):
            try:
                account = self.client.account_summary()
                break
            except Exception as exc:
                wait = min(300, 30 * attempt)
                log.warning("oanda_account_check attempt %d/5 failed (%s); retrying in %ds", attempt, exc, wait)
                time.sleep(wait)
        if account is None:
            log.warning("oanda_account_check unavailable after retries; continuing - run_cycle handles errors per cycle")
            return
        log.info(
            "oanda_account_check ok env=%s currency=%s nav_positive=%s margin_available_positive=%s",
            self.config.oanda_env,
            account.currency,
            account.nav > 0,
            account.margin_available > 0,
        )

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
        log.info("scan_started at=%s", now.isoformat())
        self._service_telegram(state)
        closed = self._sync_closed_positions(state)
        for row in closed:
            self.telegram.send(trade_closed_message(row, reason=str(row.get("exit_reason") or "not_in_oanda_open_positions")))
        time_stopped = self._apply_time_stop(state, now)
        for row in time_stopped:
            self.telegram.send(trade_closed_message(row, reason="max_hold_time_stop"))
        profit_updates, profit_errors = self._apply_profit_protection(state)
        for row in profit_updates:
            self.telegram.send(profit_lock_message(row))
        if profit_errors:
            state["last_profit_protection_errors"] = profit_errors[:5]
        no_progress_closed, no_progress_errors = self._apply_no_progress_loss_exit(state, now)
        for row in no_progress_closed:
            self.telegram.send(trade_closed_message(row, reason="no_progress_loss_exit"))
        if no_progress_errors:
            state["last_no_progress_exit_errors"] = no_progress_errors[:5]
        if state.get("paused") or state.get("halted"):
            state["last_scan"] = {"status": "paused" if state.get("paused") else "halted", "blocked_by": "paused_or_halted", "at": now.isoformat()}
            self._save_status(state, "paused")
            return state

        macro_state = self._load_macro_state()
        aggressive_risk_off = risk_off_aggressive_active(self.config, macro_state)
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
                log.info("scan_blocked reason=%s mode=%s", reason, self.config.execution_mode)
                return state

        account = self.client.account_summary()
        state["last_account"] = {
            "balance": account.balance,
            "nav": account.nav,
            "margin_available": account.margin_available,
            "margin_used": account.margin_used,
            "currency": account.currency,
        }
        log.info("oanda_account_summary_loaded currency=%s nav_positive=%s margin_available_positive=%s", account.currency, account.nav > 0, account.margin_available > 0)
        prediction_payload = load_prediction_state_payload(self.config.prediction_overlay_state_file) if self.config.prediction_overlay_enabled else None
        prediction_state = select_point_in_time_prediction_state(prediction_payload, now)
        state["prediction_overlay_status"] = _prediction_overlay_status(self.config, prediction_state)
        all_opportunities = []
        all_reasons: list[str] = []
        for symbol in self.config.universe:
            instrument = self.config.oanda_instrument_for(symbol)
            if not instrument:
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", "instrument_unavailable", 0.0)
                continue
            try:
                ok, tradeable_reason = self.client.instrument_tradeable(instrument)
                if not ok:
                    self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", tradeable_reason, 0.0)
                    continue
                quote = self.client.current_quote(symbol, instrument)
                candles_m15 = self.client.candles(instrument, count=120, granularity="M15")
                candles_h1 = self.client.candles(instrument, count=120, granularity="H1")
                candles_h4 = self.client.candles(instrument, count=120, granularity="H4") or candles_h1
            except Exception as exc:
                reason = f"market_data_error:{str(exc)[:160]}"
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", reason, 0.0)
                all_reasons.append(f"{symbol}:{reason}")
                log.info("symbol_skipped symbol=%s instrument=%s reason=%s", symbol, instrument, reason)
                continue
            if not candles_m15 or not candles_h1:
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", "candles_unavailable", 0.0)
                continue
            regime = classify_regime(self.config, symbol, candles_h1, candles_h4, macro_state, now)
            event_block = high_impact_event_block(regime.region, events, now, pre_minutes=self.config.pre_event_pause_minutes, post_minutes=self.config.post_event_settle_minutes)
            if event_block:
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", "event_pause", 0.0)
                all_reasons.append(f"{symbol}:{event_block}")
                continue
            atr_value = max(all_candle_range(candles_m15), 0.0001)
            self.spreads.add(symbol, quote.spread, now=now)
            entry_spread_cap = atr_value * spread_cap_atr(self.config, macro_state)
            spread_decision = self.spreads.evaluate(symbol, quote.spread, static_cap=entry_spread_cap, atr_cap=entry_spread_cap, now=now)
            if not spread_decision.ok:
                self._record_miss(state, symbol, "UNKNOWN", "UNKNOWN", spread_decision.reason, 0.0)
                continue
            reasons: list[str] = []
            opportunities = evaluate_all(self.config, symbol, instrument, quote, candles_m15, candles_h1, candles_h4, regime, macro_state, reasons)
            adjusted = []
            for opportunity in opportunities:
                score_offset, risk_multiplier = strategy_adjustment(calibration, symbol=symbol, strategy=opportunity.strategy, direction=opportunity.direction)
                adjusted.append(replace(opportunity, score=opportunity.score + score_offset, risk_multiplier=opportunity.risk_multiplier * risk_multiplier))
            if self.config.prediction_overlay_enabled:
                overlay_adjusted = []
                for opportunity in adjusted:
                    overlaid = apply_prediction_overlay(
                        opportunity,
                        prediction_state,
                        now,
                        enabled=self.config.prediction_overlay_enabled,
                        symbol=opportunity.symbol,
                        side=opportunity.direction,
                        stale_seconds=self.config.prediction_overlay_stale_seconds,
                        fallback_mode=self.config.prediction_overlay_fallback_mode,
                        min_favourable_probability=self.config.prediction_overlay_min_favourable_probability,
                        min_posterior=self.config.prediction_overlay_min_posterior,
                        event_given_success=self.config.prediction_overlay_event_given_success,
                        kelly_base_fraction=self.config.prediction_overlay_kelly_base_fraction,
                        max_size_multiplier=self.config.prediction_overlay_max_size_multiplier,
                        score_scale=self.config.prediction_overlay_score_scale,
                    )
                    if overlaid is None:
                        self._record_miss(state, opportunity.symbol, opportunity.direction, opportunity.strategy, "prediction_overlay_block", opportunity.score)
                    else:
                        overlay_adjusted.append(overlaid)
                adjusted = overlay_adjusted
            adjusted = [opportunity for opportunity in adjusted if macro_strategy_allowed(self.config, opportunity, macro_state)]
            all_opportunities.extend(adjusted)
            all_reasons.extend([f"{symbol}:{reason}" for reason in reasons])
        state["signals_seen"] = int(state.get("signals_seen", 0) or 0) + len(all_opportunities)
        candidates = select_best_opportunities(
            all_opportunities,
            min_score=self.config.min_score,
            score_threshold=lambda opportunity: opportunity_min_score(self.config, opportunity, macro_state, default_min_score=self.config.min_score),
        )
        cooldown_blocked = []
        if candidates:
            filtered_candidates = []
            for opportunity in candidates:
                cooldown_reason = _same_lane_stop_cooldown_reason(state, opportunity, now, self.config.same_lane_stop_cooldown_minutes)
                if cooldown_reason:
                    cooldown_blocked.append({"symbol": opportunity.symbol, "direction": opportunity.direction, "strategy": opportunity.strategy, "reason": cooldown_reason, "score": opportunity.score})
                    self._record_miss(state, opportunity.symbol, opportunity.direction, opportunity.strategy, cooldown_reason, opportunity.score)
                    log.info("scan_blocked reason=%s best_symbol=%s best_strategy=%s score=%.2f", cooldown_reason, opportunity.symbol, opportunity.strategy, opportunity.score)
                else:
                    filtered_candidates.append(opportunity)
            candidates = filtered_candidates
        if not candidates:
            if cooldown_blocked:
                state["last_scan"] = {"status": "blocked", "blocked_by": cooldown_blocked[0]["reason"], "blocked": cooldown_blocked[:5], "at": now.isoformat()}
                self._save_status(state, "blocked")
                return state
            state["last_scan"] = {"status": "idle", "blocked_by": "score_below_threshold", "reasons": all_reasons[-25:], "at": now.isoformat()}
            self._save_status(state, "idle")
            log.info("scan_idle reason=score_below_threshold opportunities=%d", len(all_opportunities))
            return state
        if self.config.execution_mode == "signal_only":
            best = candidates[0]
            state["last_scan"] = {"status": "signal_only", "best_opportunity": best.symbol, "at": now.isoformat()}
            self.telegram.send(opportunity_message(best, mode="signal_only"))
            self._save_status(state, "signal_only")
            return state
        opened: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        max_orders = max(1, self.config.max_live_orders_per_scan)
        for best in candidates:
            if len(opened) >= max_orders:
                break
            metadata = dict(best.metadata)
            if aggressive_risk_off and best.direction.upper() == "SHORT":
                metadata["risk_off_aggressive"] = True
            best_for_order = replace(best, metadata=metadata)
            details = self.client.instrument_details(best_for_order.instrument)
            position = position_from_opportunity(best_for_order, self.config, account, details, self.client.home_conversion_factor(best_for_order.instrument, best_for_order.direction))
            can_trade, reason = can_open(position, state, self.config)
            if not can_trade:
                blocked.append({"symbol": best_for_order.symbol, "direction": best_for_order.direction, "strategy": best_for_order.strategy, "reason": reason, "score": best_for_order.score})
                self._record_miss(state, best_for_order.symbol, best_for_order.direction, best_for_order.strategy, reason, best_for_order.score)
                log.info("scan_blocked reason=%s best_symbol=%s best_strategy=%s score=%.2f", reason, best_for_order.symbol, best_for_order.strategy, best_for_order.score)
                continue
            try:
                response = self.client.place_market_order(best_for_order, position.order_units)
                order_id = order_id_from_response(response)
                if not order_id:
                    raise RuntimeError("order_not_filled")
                fill_price = fill_price_from_response(response)
                if fill_price:
                    position.entry_price = fill_price
                position.order_id = order_id
                row = position_to_row(position)
                state.setdefault("open_positions", []).append(row)
                state.setdefault("events", []).append({"type": "trade_opened", "symbol": best_for_order.symbol, "direction": best_for_order.direction, "strategy": best_for_order.strategy, "order_id": order_id, "entry_price": position.entry_price, "stop_price": position.stop_price, "take_profit_price": position.take_profit_price, "units": position.units, "score": best_for_order.score, "at": now.isoformat()})
                opened.append({"symbol": best_for_order.symbol, "direction": best_for_order.direction, "strategy": best_for_order.strategy, "order_id": order_id})
                self.telegram.send(order_opened_message(position))
                log.info("trade_opened symbol=%s direction=%s strategy=%s units=%.2f order_id=%s", best_for_order.symbol, best_for_order.direction, best_for_order.strategy, position.units, order_id)
            except Exception as exc:
                reason = str(exc)
                blocked.append({"symbol": best_for_order.symbol, "direction": best_for_order.direction, "strategy": best_for_order.strategy, "reason": reason, "score": best_for_order.score})
                self._record_miss(state, best_for_order.symbol, best_for_order.direction, best_for_order.strategy, reason, best_for_order.score)
                self.telegram.send(order_rejected_message(symbol=best_for_order.symbol, direction=best_for_order.direction, strategy=best_for_order.strategy, reason=reason))
                log.info("order_not_filled symbol=%s direction=%s strategy=%s blocked_by=%s", best_for_order.symbol, best_for_order.direction, best_for_order.strategy, reason)
        if opened:
            state["last_scan"] = {"status": "trade_opened", "opened": opened, "blocked": blocked[:5], "at": now.isoformat()}
            self._save_status(state, "running")
            return state
        blocked_by = blocked[0]["reason"] if blocked else "no_order_opened"
        state["last_scan"] = {"status": "blocked", "blocked_by": blocked_by, "blocked": blocked[:5], "at": now.isoformat()}
        self._save_status(state, "blocked")
        return state

    def _sync_closed_positions(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
        if self.config.paper_trade or not self.config.has_oanda_credentials:
            return []
        open_trade_ids: set[str] = set()
        open_instruments: set[str] = set()
        used_trade_snapshot = False
        try:
            trades = self.client.open_trades()
            used_trade_snapshot = True
            for trade in trades if isinstance(trades, list) else []:
                if not isinstance(trade, dict):
                    continue
                trade_id = str(trade.get("id") or "").strip()
                if trade_id:
                    open_trade_ids.add(trade_id)
                units = _float_or_none(trade.get("currentUnits"))
                instrument = str(trade.get("instrument") or "").strip().upper()
                if instrument and (units is None or units != 0.0):
                    open_instruments.add(instrument)
        except AttributeError:
            used_trade_snapshot = False
        except Exception as exc:
            state["last_closed_position_sync_error"] = str(exc)[:200]
            log.warning("closed_position_sync_failed stage=open_trades error=%s", exc)
            return []
        if not used_trade_snapshot:
            try:
                open_instruments = self.client.open_positions()
            except Exception as exc:
                state["last_closed_position_sync_error"] = str(exc)[:200]
                log.warning("closed_position_sync_failed stage=open_positions error=%s", exc)
                return []
        kept: list[dict[str, Any]] = []
        closed: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            trade_id = str(row.get("order_id") or "").strip()
            instrument = str(row.get("instrument") or "").strip().upper()
            if used_trade_snapshot and trade_id:
                is_open = trade_id in open_trade_ids
            else:
                is_open = bool(instrument and instrument in open_instruments)
            if is_open:
                kept.append(row)
            else:
                closed.append(row)
        state["open_positions"] = kept
        closed_at = datetime.now(timezone.utc).isoformat()
        for row in closed:
            trade_id = str(row.get("order_id") or "").strip()
            row["closed_at"] = closed_at
            row["sync_reason"] = "not_in_oanda_open_positions"
            row.setdefault("exit_reason", "broker_reconciliation")
            self._enrich_row_from_recent_close(row, trade_id)
            state.setdefault("events", []).append({"type": "trade_closed", "reason": row.get("exit_reason"), "sync_reason": row.get("sync_reason"), "symbol": row.get("symbol"), "direction": row.get("direction"), "strategy": row.get("strategy"), "instrument": row.get("instrument"), "order_id": row.get("order_id"), "realized_pl": row.get("realized_pl"), "closed_at": row.get("closed_at"), "at": closed_at})
            log.info("trade_closed symbol=%s direction=%s strategy=%s reason=%s order_id=%s realized_pl=%s", row.get("symbol"), row.get("direction"), row.get("strategy"), row.get("exit_reason"), row.get("order_id"), row.get("realized_pl"))
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
                    close_response = self.client.close_trade(trade_id)
                    self._enrich_row_from_close(row, close_response)
                except RuntimeError as exc:
                    log.warning("time_stop_close_failed instrument=%s order_id=%s error=%s", row.get("instrument"), trade_id, exc)
                    kept.append(row)
                    continue
            stopped.append(row)
            state.setdefault("events", []).append({
                "type": "trade_closed",
                "reason": "max_hold_time_stop",
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "strategy": row.get("strategy"),
                "instrument": row.get("instrument"),
                "order_id": trade_id,
                "age_minutes": int((now - opened_at).total_seconds() // 60),
                "at": now.isoformat(),
            })
            log.info("trade_closed symbol=%s direction=%s strategy=%s reason=max_hold_time_stop order_id=%s", row.get("symbol"), row.get("direction"), row.get("strategy"), trade_id)
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
            trigger_pct, pullback_trigger_pct = profit_lock_thresholds(self.config, metadata)
            if peak_pnl_pct < max(0.0, trigger_pct) or pullback_pct < max(0.0, pullback_trigger_pct) or pnl_pct <= 0.0:
                kept_rows.append(row)
                continue
            if broker_available:
                try:
                    close_response = self.client.close_trade(trade_id)
                    self._enrich_row_from_close(row, close_response)
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

    def _apply_no_progress_loss_exit(self, state: dict[str, Any], now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        updates: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        if not self.config.no_progress_exit_enabled:
            state["last_no_progress_exit_updates"] = []
            return updates, errors
        broker_available = not self.config.paper_trade and self.config.live_trading_enabled and self.config.has_oanda_credentials
        if not broker_available:
            state["last_no_progress_exit_updates"] = []
            return updates, errors
        rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
        if not rows:
            state["last_no_progress_exit_updates"] = []
            return updates, errors
        try:
            trades = self.client.open_trades()
        except RuntimeError as exc:
            errors.append({"stage": "no_progress_open_trades", "error": str(exc)})
            state["last_no_progress_exit_errors"] = errors[:5]
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
        min_age = timedelta(minutes=max(1, int(self.config.no_progress_min_bars)) * max(1, int(self.config.bar_minutes)))
        min_peak_r = max(0.0, float(self.config.no_progress_min_peak_r))
        loss_r = max(0.0, float(self.config.no_progress_loss_r))
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
            opened_at = _parse_datetime(row.get("opened_at"))
            if opened_at is None or now - opened_at < min_age:
                kept_rows.append(row)
                continue
            current_r = _position_unrealized_r(row)
            if current_r is None:
                kept_rows.append(row)
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            metadata = dict(metadata)
            peak_r = _float_or_none(metadata.get("no_progress_peak_r"))
            if peak_r is None or current_r > peak_r:
                peak_r = current_r
                metadata["no_progress_peak_r"] = peak_r
                metadata["no_progress_peak_seen_at"] = now.isoformat()
            row["metadata"] = metadata
            if peak_r >= min_peak_r or current_r > -loss_r:
                kept_rows.append(row)
                continue
            try:
                close_response = self.client.close_trade(trade_id)
                self._enrich_row_from_close(row, close_response)
            except RuntimeError as exc:
                errors.append({"symbol": row.get("symbol") or instrument, "instrument": instrument, "stage": "no_progress_close", "error": str(exc)})
                kept_rows.append(row)
                continue
            row["closed_at"] = now.isoformat()
            row["exit_reason"] = "no_progress_loss_exit"
            row["no_progress_peak_r"] = peak_r
            row["no_progress_current_r"] = current_r
            updates.append(dict(row))
            state.setdefault("events", []).append({"type": "trade_closed", "reason": "no_progress_loss_exit", "symbol": row.get("symbol"), "direction": row.get("direction"), "strategy": row.get("strategy"), "instrument": instrument, "order_id": trade_id, "peak_r": peak_r, "current_r": current_r, "at": now.isoformat()})
            log.info("trade_closed symbol=%s direction=%s strategy=%s reason=no_progress_loss_exit order_id=%s current_r=%.3f peak_r=%.3f", row.get("symbol"), row.get("direction"), row.get("strategy"), trade_id, current_r, peak_r)
        state["open_positions"] = kept_rows
        state["last_no_progress_exit_updates"] = updates
        state["last_no_progress_exit_errors"] = errors[:5]
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

    def _enrich_row_from_recent_close(self, row: dict[str, Any], trade_id: str) -> None:
        if not trade_id or not hasattr(self.client, "recent_trade_close"):
            return
        try:
            close = self.client.recent_trade_close(trade_id)
        except Exception as exc:
            row["broker_close_lookup_error"] = str(exc)[:200]
            log.info("trade_close_lookup_failed instrument=%s order_id=%s error=%s", row.get("instrument"), trade_id, exc)
            return
        if isinstance(close, dict):
            self._enrich_row_from_close(row, close)

    def _enrich_row_from_close(self, row: dict[str, Any], close: dict[str, object]) -> None:
        transaction = close.get("orderFillTransaction") if isinstance(close.get("orderFillTransaction"), dict) else close
        if not isinstance(transaction, dict):
            return
        reason = str(transaction.get("reason") or "").strip().lower()
        if reason:
            row["exit_reason"] = reason
            row["broker_close_reason"] = reason
        closed_at = transaction.get("time")
        if closed_at:
            row["closed_at"] = str(closed_at).replace("Z", "+00:00")
        price = _float_or_none(transaction.get("price"))
        if price is not None and price > 0:
            row["exit_price"] = price
            row["close_price"] = price
        close_parts = _trade_close_parts(transaction)
        realized_values = [_float_or_none(part.get("realizedPL")) for part in close_parts]
        if not any(value is not None for value in realized_values):
            realized_values = [_float_or_none(transaction.get("pl"))]
        realized = sum(value for value in realized_values if value is not None)
        if any(value is not None for value in realized_values):
            row["realized_pl"] = realized
            row["unrealized_pl"] = realized
            entry_budget = _position_entry_budget(row)
            if entry_budget is not None and entry_budget > 0:
                row["pnl_pct"] = realized / entry_budget * 100.0
        financing_values = [_float_or_none(part.get("financing")) for part in close_parts]
        if any(value is not None for value in financing_values):
            row["financing"] = sum(value for value in financing_values if value is not None)
        spread_values = [_float_or_none(part.get("halfSpreadCost")) for part in close_parts]
        if any(value is not None for value in spread_values):
            row["half_spread_cost"] = sum(value for value in spread_values if value is not None)

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
                self.telegram.send(_events_message(self._load_macro_state()))
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

    def _load_macro_state(self) -> dict[str, Any]:
        if not self.config.news_enabled:
            return default_macro_state(source_status="disabled")
        return load_macro_state(
            self.config.macro_state_file,
            redis_url=self.config.redis_url,
            redis_key=self.config.macro_state_key,
            max_age_minutes=self.config.macro_state_max_age_minutes,
        )

    def _record_miss(self, state: dict[str, Any], symbol: str, direction: str, strategy: str, reason: str, score: float) -> None:
        state.setdefault("missed_opportunities", []).append({"symbol": symbol, "direction": direction, "strategy": strategy, "blocked_by": reason, "score": score, "at": datetime.now(timezone.utc).isoformat()})

    def _save_status(self, state: dict[str, Any], status: str) -> None:
        self.state_store.save(state)
        write_daily_review(self.config.daily_review_file, state)
        self.state_store.publish_status(self.config.bot_status_key, _runtime_status_payload(state, status))


def all_candle_range(candles: list[Any]) -> float:
    values = [max(0.0, candle.high - candle.low) for candle in candles[-14:]]
    return sum(values) / len(values) if values else 0.0


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _trade_close_parts(transaction: dict[str, object]) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    for key in ("tradesClosed", "tradesReduced"):
        rows = transaction.get(key)
        if isinstance(rows, list):
            parts.extend(row for row in rows if isinstance(row, dict))
    for key in ("tradeClosed", "tradeReduced"):
        row = transaction.get(key)
        if isinstance(row, dict):
            parts.append(row)
    return parts


def _position_entry_budget(row: dict[str, Any]) -> float | None:
    for key in ("entry_budget", "initial_margin_required", "margin_used"):
        value = _float_or_none(row.get(key))
        if value is not None and value > 0:
            return value
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    value = _float_or_none(metadata.get("risk_amount"))
    return value if value is not None and value > 0 else None


def _position_risk_amount(row: dict[str, Any]) -> float | None:
    for key in ("risk_amount", "risk_at_sl", "stop_risk"):
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


def _position_unrealized_r(row: dict[str, Any]) -> float | None:
    unrealized_pl = _position_unrealized_pl(row)
    risk_amount = _position_risk_amount(row)
    if unrealized_pl is None or risk_amount is None or risk_amount <= 0:
        return None
    return unrealized_pl / risk_amount


def _same_lane_stop_cooldown_reason(state: dict[str, Any], opportunity: Any, now: datetime, cooldown_minutes: int) -> str:
    cooldown = timedelta(minutes=max(0, int(cooldown_minutes)))
    if cooldown.total_seconds() <= 0:
        return ""
    cutoff = now - cooldown
    symbol = str(getattr(opportunity, "symbol", "") or "").upper()
    direction = str(getattr(opportunity, "direction", "") or "").upper()
    strategy = str(getattr(opportunity, "strategy", "") or "").upper()
    for event in reversed(state.get("events", []) if isinstance(state.get("events"), list) else []):
        if not isinstance(event, dict) or event.get("type") != "trade_closed":
            continue
        closed_at = _parse_datetime(event.get("at") or event.get("closed_at"))
        if closed_at is None or closed_at < cutoff:
            continue
        if str(event.get("symbol") or "").upper() != symbol:
            continue
        if str(event.get("direction") or "").upper() != direction:
            continue
        if str(event.get("strategy") or "").upper() != strategy:
            continue
        if not _is_stop_loss_exit(event.get("reason") or event.get("exit_reason")):
            continue
        remaining = max(1, int((cooldown - (now - closed_at)).total_seconds() // 60))
        return f"same_lane_stop_cooldown:{remaining}m"
    return ""


def _is_stop_loss_exit(value: object) -> bool:
    reason = str(value or "").strip().lower()
    return reason in {"stop", "stop_loss_order", "stop_and_target_same_bar"} or "stop_loss" in reason


def _runtime_open_metrics(rows: list[Any]) -> tuple[float, float, float, int]:
    allocated = 0.0
    pnl_amount = 0.0
    pnl_base = 0.0
    open_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        open_count += 1
        entry_budget = _position_entry_budget(row)
        if entry_budget is not None:
            allocated += entry_budget
            pnl_base += entry_budget
        unrealized_pl = _position_unrealized_pl(row)
        if unrealized_pl is not None:
            pnl_amount += unrealized_pl
    pnl_pct = pnl_amount / pnl_base * 100.0 if pnl_base > 0 else 0.0
    return allocated, pnl_amount, pnl_pct, open_count


def _runtime_status_payload(state: dict[str, Any], status: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    rows = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
    allocated, pnl_amount, pnl_pct, open_count = _runtime_open_metrics(rows)
    account = state.get("last_account") if isinstance(state.get("last_account"), dict) else {}
    nav = _float_or_none(account.get("nav"))
    balance = _float_or_none(account.get("balance"))
    margin_available = _float_or_none(account.get("margin_available"))
    margin_used = _float_or_none(account.get("margin_used"))
    if margin_used is not None:
        allocated = margin_used
    total_closed = len([event for event in state.get("events", []) if isinstance(event, dict) and event.get("type") == "trade_closed"])
    return {
        "service": "indices",
        "state": status,
        "status": status,
        "generated_at": now,
        "updated_at": now,
        "account_balance": nav if nav is not None else balance,
        "account_nav": nav,
        "balance": balance,
        "available_balance": margin_available,
        "allocated_balance": allocated,
        "margin_used": margin_used,
        "unrealized_pl": pnl_amount,
        "pnl_amount": pnl_amount,
        "pnl_pct": pnl_pct,
        "open_trades": open_count,
        "total_trades": total_closed + open_count,
        "profit_factor": None,
        "open_positions": rows,
        "last_scan": state.get("last_scan", {}),
        "prediction_overlay_status": state.get("prediction_overlay_status"),
    }


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
