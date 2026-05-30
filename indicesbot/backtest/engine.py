from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from math import ceil
from typing import Any

from indicesbot.calibration import strategy_adjustment
from indicesbot.config import IndicesConfig
from indicesbot.models import IndexQuote
from indicesbot.prediction_overlay import apply_prediction_overlay, load_prediction_state_payload, select_point_in_time_prediction_state
from indicesbot.regimes import classify_regime
from indicesbot.risk_off import macro_strategy_allowed, opportunity_min_score
from indicesbot.strategies import evaluate_all, select_best_opportunity


def run_backtest(config: IndicesConfig, candles_by_symbol: dict[str, list], macro_state: dict | None = None, *, min_score: float = 72.0, max_hold_bars: int = 16, calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    macro = macro_state or {"risk_regime": {"global": "MIXED"}, "event_scores": []}
    prediction_payload = load_prediction_state_payload(config.prediction_overlay_state_file) if config.prediction_overlay_enabled else None
    trades: list[dict[str, Any]] = []
    equity = config.backtest_initial_balance
    peak_equity = equity
    max_drawdown = 0.0
    cooldown_until: dict[str, int] = {}
    for symbol, candles in candles_by_symbol.items():
        instrument = config.oanda_instrument_for(symbol) or symbol
        for index in range(80, len(candles), 12):
            window = candles[: index + 1]
            spread = _estimated_spread(window[-1].close, window[-30:])
            quote = IndexQuote(symbol, instrument, window[-1].close - spread / 2.0, window[-1].close + spread / 2.0, window[-1].close, spread, True, "backtest", window[-1].time)
            regime = classify_regime(config, symbol, window[-80:], window[-80:], macro, window[-1].time)
            reasons: list[str] = []
            opportunities = evaluate_all(config, symbol, instrument, quote, window[-120:], window[-80:], window[-80:], regime, macro, reasons)
            if calibration:
                opportunities = [_apply_calibration(opportunity, calibration) for opportunity in opportunities]
            if config.prediction_overlay_enabled:
                prediction_state = select_point_in_time_prediction_state(prediction_payload, window[-1].time)
                opportunities = [
                    item
                    for item in (
                        apply_prediction_overlay(
                            opportunity,
                            prediction_state,
                            window[-1].time,
                            enabled=config.prediction_overlay_enabled,
                            stale_seconds=config.prediction_overlay_stale_seconds,
                            fallback_mode=config.prediction_overlay_fallback_mode,
                            min_favourable_probability=config.prediction_overlay_min_favourable_probability,
                            min_posterior=config.prediction_overlay_min_posterior,
                            event_given_success=config.prediction_overlay_event_given_success,
                            kelly_base_fraction=config.prediction_overlay_kelly_base_fraction,
                            max_size_multiplier=config.prediction_overlay_max_size_multiplier,
                            score_scale=config.prediction_overlay_score_scale,
                        )
                        for opportunity in opportunities
                    )
                    if item is not None
                ]
            opportunities = [opportunity for opportunity in opportunities if macro_strategy_allowed(config, opportunity, macro)]
            opportunities = [opportunity for opportunity in opportunities if index > cooldown_until.get(_cooldown_key(opportunity), -1)]
            best = select_best_opportunity(
                opportunities,
                min_score=min_score,
                score_threshold=lambda opportunity: opportunity_min_score(config, opportunity, macro, default_min_score=min_score),
            )
            if best is None:
                continue
            exit_price, exit_reason, held_bars = _simulate_exit(candles, index, best, max_hold_bars=max_hold_bars, config=config)
            pnl_points = (exit_price - best.entry_price) if best.direction == "LONG" else (best.entry_price - exit_price)
            stop_distance = max(abs(best.entry_price - best.stop_price), 0.0001)
            r_multiple = pnl_points / stop_distance
            risk_amount = equity * config.budget_allocation * config.max_risk_per_trade * best.risk_multiplier
            pnl = risk_amount * r_multiple
            equity += pnl
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity if peak_equity else 0.0)
            trades.append({
                "symbol": symbol,
                "direction": best.direction,
                "strategy": best.strategy,
                "entry": best.entry_price,
                "exit": exit_price,
                "pnl": pnl,
                "pnl_points": pnl_points,
                "risk_amount": risk_amount,
                "r_multiple": r_multiple,
                "exit_reason": exit_reason,
                "held_bars": held_bars,
                "opened_at": best.metadata.get("time", window[-1].time.isoformat()),
            })
            if _is_stop_exit(exit_reason):
                cooldown_bars = ceil(max(0, int(getattr(config, "same_lane_stop_cooldown_minutes", 0))) / max(1, int(getattr(config, "bar_minutes", 15))))
                if cooldown_bars > 0:
                    cooldown_until[_cooldown_key(best)] = index + held_bars + cooldown_bars
    wins = [trade for trade in trades if trade["pnl"] > 0]
    losses = [trade for trade in trades if trade["pnl"] < 0]
    gross_win = sum(trade["pnl"] for trade in wins)
    gross_loss = abs(sum(trade["pnl"] for trade in losses))
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_trades": len(trades),
        "total_pnl": sum(trade["pnl"] for trade in trades),
        "return_pct": (equity - config.backtest_initial_balance) / config.backtest_initial_balance,
        "profit_factor": gross_win / gross_loss if gross_loss else (gross_win if gross_win else 0.0),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "max_drawdown": max_drawdown,
        "trades": trades,
        "groups": _groups(trades),
    }
    return summary


def calibration_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "groups": summary.get("groups", {}),
        "total_trades": summary.get("total_trades", 0),
        "profit_factor": summary.get("profit_factor", 0.0),
        "win_rate": summary.get("win_rate", 0.0),
    }


def _apply_calibration(opportunity: Any, calibration: dict[str, Any]) -> Any:
    score_offset, risk_multiplier = strategy_adjustment(calibration, symbol=opportunity.symbol, strategy=opportunity.strategy, direction=opportunity.direction)
    return replace(opportunity, score=opportunity.score + score_offset, risk_multiplier=opportunity.risk_multiplier * risk_multiplier)


def _cooldown_key(opportunity: Any) -> str:
    return f"{opportunity.symbol}:{opportunity.strategy}:{opportunity.direction}".upper()


def _is_stop_exit(reason: str) -> bool:
    normalized = str(reason or "").strip().lower()
    return normalized in {"stop", "stop_loss_order", "stop_and_target_same_bar"} or "stop_loss" in normalized


def _simulate_exit(candles: list, entry_index: int, opportunity: Any, *, max_hold_bars: int, config: IndicesConfig | None = None) -> tuple[float, str, int]:
    future = candles[entry_index + 1 : min(len(candles), entry_index + max(1, max_hold_bars) + 1)]
    if not future:
        return candles[entry_index].close, "end_of_data", 0
    target = opportunity.take_profit_price
    stop_distance = max(abs(opportunity.entry_price - opportunity.stop_price), 0.0001)
    profit_lock_enabled = bool(getattr(config, "profit_lock_enabled", False))
    trigger_r = max(0.0, float(getattr(config, "profit_lock_trigger_pct", 0.0))) / 100.0
    pullback_r = max(0.0, float(getattr(config, "profit_lock_pullback_pct", 0.0))) / 100.0
    no_progress_enabled = bool(getattr(config, "no_progress_exit_enabled", False))
    no_progress_min_bars = max(1, int(getattr(config, "no_progress_min_bars", 0)))
    no_progress_min_peak_r = max(0.0, float(getattr(config, "no_progress_min_peak_r", 0.0)))
    no_progress_loss_r = max(0.0, float(getattr(config, "no_progress_loss_r", 0.0)))
    peak_r = 0.0
    lock_armed = False
    lock_floor_r = 0.0
    for held_bars, candle in enumerate(future, start=1):
        armed_before = lock_armed
        if opportunity.direction == "LONG":
            peak_r = max(peak_r, (candle.high - opportunity.entry_price) / stop_distance)
            if profit_lock_enabled:
                if peak_r >= trigger_r:
                    lock_armed = True
                    lock_floor_r = max(0.0, peak_r - pullback_r)
                if armed_before and lock_floor_r > 0.0:
                    lock_price = opportunity.entry_price + lock_floor_r * stop_distance
                    if candle.low <= lock_price:
                        return lock_price, "peak_pullback_profit_lock", held_bars
            stopped = candle.low <= opportunity.stop_price
            targeted = target is not None and candle.high >= target
            if stopped and targeted:
                return opportunity.stop_price, "stop_and_target_same_bar", held_bars
            if stopped:
                return opportunity.stop_price, "stop", held_bars
            if targeted:
                return float(target), "target", held_bars
            current_r = (candle.close - opportunity.entry_price) / stop_distance
            if no_progress_enabled and held_bars >= no_progress_min_bars and peak_r < no_progress_min_peak_r and current_r <= -no_progress_loss_r:
                return candle.close, "no_progress_loss_exit", held_bars
        else:
            peak_r = max(peak_r, (opportunity.entry_price - candle.low) / stop_distance)
            if profit_lock_enabled:
                if peak_r >= trigger_r:
                    lock_armed = True
                    lock_floor_r = max(0.0, peak_r - pullback_r)
                if armed_before and lock_floor_r > 0.0:
                    lock_price = opportunity.entry_price - lock_floor_r * stop_distance
                    if candle.high >= lock_price:
                        return lock_price, "peak_pullback_profit_lock", held_bars
            stopped = candle.high >= opportunity.stop_price
            targeted = target is not None and candle.low <= target
            if stopped and targeted:
                return opportunity.stop_price, "stop_and_target_same_bar", held_bars
            if stopped:
                return opportunity.stop_price, "stop", held_bars
            if targeted:
                return float(target), "target", held_bars
            current_r = (opportunity.entry_price - candle.close) / stop_distance
            if no_progress_enabled and held_bars >= no_progress_min_bars and peak_r < no_progress_min_peak_r and current_r <= -no_progress_loss_r:
                return candle.close, "no_progress_loss_exit", held_bars
    return future[-1].close, "time_exit", len(future)


def _estimated_spread(price: float, candles: list) -> float:
    ranges = [max(0.0, candle.high - candle.low) for candle in candles[-14:]]
    average_range = sum(ranges) / len(ranges) if ranges else 0.0
    return max(0.01, price * 0.00005, average_range * 0.015)


def _groups(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        for key in (str(trade["strategy"]).upper(), f"{trade['strategy']}:{trade['symbol']}:{trade['direction']}".upper()):
            grouped.setdefault(key, []).append(trade)
    result = {}
    for key, rows in grouped.items():
        wins = [row for row in rows if row["pnl"] > 0]
        losses = [row for row in rows if row["pnl"] < 0]
        gross_win = sum(row["pnl"] for row in wins)
        gross_loss = abs(sum(row["pnl"] for row in losses))
        result[key] = {"trades": len(rows), "pnl": sum(row["pnl"] for row in rows), "profit_factor": gross_win / gross_loss if gross_loss else (gross_win if gross_win else 0.0)}
    return result
