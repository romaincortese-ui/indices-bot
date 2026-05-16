from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from indicesbot.backtest.data import market_candles_by_symbol
from indicesbot.backtest.engine import calibration_from_summary, run_backtest
from indicesbot.calibration import load_calibration, write_calibration
from indicesbot.config import load_config
from indicesbot.daily_review import write_daily_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--data-source", choices=("synthetic", "online", "auto", "oanda", "yahoo"), default="synthetic")
    parser.add_argument("--granularity", default=None)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--min-score", type=float, default=72.0)
    parser.add_argument("--max-hold-bars", type=int, default=16)
    parser.add_argument("--use-calibration", default="")
    parser.add_argument("--calibrate-first", action="store_true")
    parser.add_argument("--walk-forward", type=float, default=0.0, help="Fraction (0-1) of bars to use for in-sample calibration; remainder is out-of-sample test. 0 disables.")
    parser.add_argument("--macro-scenario", choices=("mixed", "risk-off", "risk-on"), default="mixed")
    args = parser.parse_args(argv)
    config = load_config()
    days = args.days or config.backtest_days
    granularity = args.granularity or config.backtest_granularity
    symbols = tuple(part.strip().upper() for part in args.symbols.replace(",", " ").split() if part.strip()) or config.universe
    macro_state = _macro_state_for_scenario(args.macro_scenario)
    output_dir = config.backtest_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    candles_by_symbol, data_sources = market_candles_by_symbol(config, days=days, source=args.data_source, granularity=granularity, symbols=symbols)
    baseline_summary = None
    calibration = None
    calibration_source = "none"
    if args.use_calibration:
        calibration = load_calibration(Path(args.use_calibration))
        calibration_source = args.use_calibration if calibration else "missing"
    elif args.calibrate_first:
        baseline_summary = run_backtest(config, candles_by_symbol, macro_state=macro_state, min_score=72.0, max_hold_bars=16)
        calibration = calibration_from_summary(baseline_summary)
        calibration_source = "current_backtest_baseline"
    if args.walk_forward and 0.0 < args.walk_forward < 1.0:
        in_sample: dict[str, list] = {}
        out_sample: dict[str, list] = {}
        for symbol, bars in candles_by_symbol.items():
            split = max(1, int(len(bars) * args.walk_forward))
            in_sample[symbol] = bars[:split]
            out_sample[symbol] = bars[split:]
        is_summary = run_backtest(config, in_sample, macro_state=macro_state, min_score=args.min_score, max_hold_bars=args.max_hold_bars)
        is_calibration = calibration_from_summary(is_summary)
        oos_summary = run_backtest(config, out_sample, macro_state=macro_state, min_score=args.min_score, max_hold_bars=args.max_hold_bars, calibration=is_calibration)
        summary = oos_summary
        summary["walk_forward"] = {
            "in_sample_fraction": args.walk_forward,
            "in_sample": {key: is_summary.get(key) for key in ("total_trades", "total_pnl", "return_pct", "profit_factor", "win_rate", "max_drawdown")},
            "out_of_sample": {key: oos_summary.get(key) for key in ("total_trades", "total_pnl", "return_pct", "profit_factor", "win_rate", "max_drawdown")},
        }
        calibration = is_calibration
        calibration_source = "walk_forward_in_sample"
    else:
        summary = run_backtest(config, candles_by_symbol, macro_state=macro_state, min_score=args.min_score, max_hold_bars=args.max_hold_bars, calibration=calibration)
    summary["data_source"] = args.data_source
    summary["data_sources"] = data_sources
    summary["days"] = days
    summary["granularity"] = granularity
    summary["macro_scenario"] = args.macro_scenario
    summary["symbols"] = sorted(candles_by_symbol)
    summary["min_score"] = args.min_score
    summary["max_hold_bars"] = args.max_hold_bars
    summary["calibration_source"] = calibration_source
    if baseline_summary:
        summary["baseline"] = {key: baseline_summary[key] for key in ("total_trades", "total_pnl", "return_pct", "profit_factor", "win_rate", "max_drawdown")}
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    with (output_dir / "trade_journal.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["symbol", "direction", "strategy", "entry", "exit", "pnl", "exit_reason", "held_bars", "opened_at"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([{key: trade.get(key) for key in fieldnames} for trade in summary["trades"]])
    with (output_dir / "equity_curve.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["index", "equity"])
        writer.writeheader()
        equity = config.backtest_initial_balance
        for index, trade in enumerate(summary["trades"]):
            equity += float(trade["pnl"])
            writer.writerow({"index": index, "equity": equity})
    calibration_payload = write_calibration(config.calibration_file, calibration or calibration_from_summary(summary))
    write_daily_review(config.daily_review_file, {"events": [], "missed_opportunities": [], "signals_seen": summary["total_trades"]}, summary)
    response = {"summary": {key: summary[key] for key in ("total_trades", "total_pnl", "return_pct", "profit_factor", "win_rate", "max_drawdown")}, "data_sources": data_sources, "macro_scenario": args.macro_scenario, "calibration_source": calibration_source, "output_dir": str(output_dir), "calibration": calibration_payload["generated_at"]}
    if baseline_summary:
        response["baseline"] = summary["baseline"]
    print(json.dumps(response, indent=2))
    return 0


def _macro_state_for_scenario(scenario: str) -> dict:
    normalized = scenario.lower().strip()
    if normalized == "risk-off":
        return {
            "risk_regime": {"global": "RISK_OFF", "vix": 28.0, "vix_change_pct": 12.0, "us10y_change_bps": -8.0, "dxy_change_pct": 0.6},
            "event_scores": [{"event_id": "scenario-risk-off", "region": "GLOBAL", "direction": "SHORT", "score": -0.7, "confidence": 0.8, "reason": "Backtest risk-off scenario"}],
            "events": [],
        }
    if normalized == "risk-on":
        return {
            "risk_regime": {"global": "RISK_ON", "vix": 15.0, "vix_change_pct": -5.0, "us10y_change_bps": 2.0, "dxy_change_pct": -0.2},
            "event_scores": [{"event_id": "scenario-risk-on", "region": "GLOBAL", "direction": "LONG", "score": 0.5, "confidence": 0.7, "reason": "Backtest risk-on scenario"}],
            "events": [],
        }
    return {"risk_regime": {"global": "MIXED", "vix_change_pct": 0.0, "us10y_change_bps": 0.0, "dxy_change_pct": 0.0}, "event_scores": [], "events": []}


if __name__ == "__main__":
    raise SystemExit(main())
