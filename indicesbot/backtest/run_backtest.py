from __future__ import annotations

import argparse
import csv
import json

from indicesbot.backtest.data import synthetic_candles
from indicesbot.backtest.engine import run_backtest
from indicesbot.calibration import write_calibration
from indicesbot.config import load_config
from indicesbot.daily_review import write_daily_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args(argv)
    config = load_config()
    days = args.days or config.backtest_days
    output_dir = config.backtest_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    candles_by_symbol = {symbol: synthetic_candles(days, start_price=100.0 + index * 50.0) for index, symbol in enumerate(config.universe[:3])}
    summary = run_backtest(config, candles_by_symbol)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    with (output_dir / "trade_journal.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["symbol", "direction", "strategy", "entry", "exit", "pnl", "opened_at"]
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
    calibration = write_calibration(config.calibration_file, {"groups": summary["groups"], "total_trades": summary["total_trades"], "profit_factor": summary["profit_factor"], "win_rate": summary["win_rate"]})
    write_daily_review(config.daily_review_file, {"events": [], "missed_opportunities": [], "signals_seen": summary["total_trades"]}, summary)
    print(json.dumps({"summary": {key: summary[key] for key in ("total_trades", "total_pnl", "profit_factor", "win_rate", "max_drawdown")}, "output_dir": str(output_dir), "calibration": calibration["generated_at"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
