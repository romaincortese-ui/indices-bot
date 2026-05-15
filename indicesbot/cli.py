from __future__ import annotations

import argparse
import json
import logging

from indicesbot.backtest.run_backtest import main as backtest_main
from indicesbot.config import load_config
from indicesbot.daily_review import write_daily_review
from indicesbot.macro_state import refresh_macro_state
from indicesbot.runtime import IndicesRuntime
from indicesbot.state import StateStore


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="indices-bot")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Run the indices bot runtime")
    sub.add_parser("macro", help="Refresh macro/news state")
    sub.add_parser("calibrate", help="Run rolling backtest calibration")
    sub.add_parser("review", help="Write daily review from current state")
    sub.add_parser("validate-config", help="Load and print safe config summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"
    config = load_config()
    if command == "run":
        IndicesRuntime(config).run_forever()
        return 0
    if command == "macro":
        print(json.dumps(refresh_macro_state(config), indent=2))
        return 0
    if command == "calibrate":
        return backtest_main([])
    if command == "review":
        state = StateStore(path=config.state_file, redis_url=config.redis_url, redis_key=config.runtime_state_key).load()
        print(json.dumps(write_daily_review(config.daily_review_file, state), indent=2))
        return 0
    if command == "validate-config":
        print(json.dumps({
            "execution_mode": config.execution_mode,
            "paper_trade": config.paper_trade,
            "universe": config.universe,
            "enabled_strategies": config.enabled_strategies,
            "require_calibration_for_trading": config.require_calibration_for_trading,
            "calibration_min_trades": config.calibration_min_trades,
            "startup_message_cooldown_minutes": config.startup_message_cooldown_minutes,
            "telegram_configured": bool(config.telegram_token and config.telegram_chat_id),
            "oanda_configured": config.has_oanda_credentials,
        }, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
