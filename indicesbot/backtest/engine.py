from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from indicesbot.calibration import strategy_adjustment
from indicesbot.config import IndicesConfig
from indicesbot.models import IndexQuote
from indicesbot.regimes import classify_regime
from indicesbot.risk_off import macro_strategy_allowed, opportunity_min_score
from indicesbot.strategies import evaluate_all, select_best_opportunity


def run_backtest(config: IndicesConfig, candles_by_symbol: dict[str, list], macro_state: dict | None = None, *, min_score: float = 72.0, max_hold_bars: int = 16, calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    macro = macro_state or {"risk_regime": {"global": "MIXED"}, "event_scores": []}
    trades: list[dict[str, Any]] = []
    equity = config.backtest_initial_balance
    peak_equity = equity
    max_drawdown = 0.0
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
            opportunities = [opportunity for opportunity in opportunities if macro_strategy_allowed(config, opportunity, macro)]
            best = select_best_opportunity(
                opportunities,
                min_score=min_score,
                score_threshold=lambda opportunity: opportunity_min_score(config, opportunity, macro, default_min_score=min_score),
            )
            if best is None:
                continue
            exit_price, exit_reason, held_bars = _simulate_exit(candles, index, best, max_hold_bars=max_hold_bars)
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


def _simulate_exit(candles: list, entry_index: int, opportunity: Any, *, max_hold_bars: int) -> tuple[float, str, int]:
    future = candles[entry_index + 1 : min(len(candles), entry_index + max(1, max_hold_bars) + 1)]
    if not future:
        return candles[entry_index].close, "end_of_data", 0
    target = opportunity.take_profit_price
    for held_bars, candle in enumerate(future, start=1):
        if opportunity.direction == "LONG":
            stopped = candle.low <= opportunity.stop_price
            targeted = target is not None and candle.high >= target
            if stopped and targeted:
                return opportunity.stop_price, "stop_and_target_same_bar", held_bars
            if stopped:
                return opportunity.stop_price, "stop", held_bars
            if targeted:
                return float(target), "target", held_bars
        else:
            stopped = candle.high >= opportunity.stop_price
            targeted = target is not None and candle.low <= target
            if stopped and targeted:
                return opportunity.stop_price, "stop_and_target_same_bar", held_bars
            if stopped:
                return opportunity.stop_price, "stop", held_bars
            if targeted:
                return float(target), "target", held_bars
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
