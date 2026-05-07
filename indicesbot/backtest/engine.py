from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from indicesbot.config import IndicesConfig
from indicesbot.models import IndexQuote
from indicesbot.regimes import classify_regime
from indicesbot.strategies import evaluate_all, select_best_opportunity


def run_backtest(config: IndicesConfig, candles_by_symbol: dict[str, list], macro_state: dict | None = None) -> dict[str, Any]:
    macro = macro_state or {"risk_regime": {"global": "MIXED"}, "event_scores": []}
    trades: list[dict[str, Any]] = []
    equity = config.backtest_initial_balance
    for symbol, candles in candles_by_symbol.items():
        instrument = config.oanda_instrument_for(symbol) or symbol
        for index in range(80, len(candles), 12):
            window = candles[: index + 1]
            quote = IndexQuote(symbol, instrument, window[-1].close - 0.05, window[-1].close + 0.05, window[-1].close, 0.10, True, "backtest", window[-1].time)
            regime = classify_regime(config, symbol, window[-80:], window[-80:], macro, window[-1].time)
            reasons: list[str] = []
            opportunities = evaluate_all(config, symbol, instrument, quote, window[-120:], window[-80:], window[-80:], regime, macro, reasons)
            best = select_best_opportunity(opportunities, min_score=72)
            if best is None:
                continue
            exit_price = candles[min(len(candles) - 1, index + 8)].close
            pnl = (exit_price - best.entry_price) if best.direction == "LONG" else (best.entry_price - exit_price)
            equity += pnl
            trades.append({"symbol": symbol, "direction": best.direction, "strategy": best.strategy, "entry": best.entry_price, "exit": exit_price, "pnl": pnl, "opened_at": best.metadata.get("time", window[-1].time.isoformat())})
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
        "max_drawdown": 0.0,
        "trades": trades,
        "groups": _groups(trades),
    }
    return summary


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
