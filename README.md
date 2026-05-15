# Indices Bot

Standalone OANDA indices trading bot with safe defaults, Telegram alerts, long/short opportunity review, news-aware risk controls, and a backtest/calibration path.

The bot is ready to run in paper or signal-only mode. To use it, add your OANDA and Telegram values to environment variables or copy `.env.example` to `.env` locally.

## What It Trades

Default universe:

- `SPX500`
- `NAS100`
- `US30`
- `UK100`
- `DE40`
- `EU50`
- `FR40`
- `JP225`
- `HK33`
- `AU200`

Every index can be evaluated for both `LONG` and `SHORT` opportunities.

## Architecture

- `indicesbot/config.py`: environment parsing and live-mode validation
- `indicesbot/oanda_client.py`: OANDA account, pricing, candles, instruments, and orders
- `indicesbot/runtime.py`: runtime owner for scans, Telegram commands, state, and broker actions
- `indicesbot/strategies/`: pure long/short strategy scorers
- `indicesbot/risk.py`: stop-distance, margin, symbol, region, and portfolio caps
- `indicesbot/news.py` and `indicesbot/macro_state.py`: cached macro/news state and event gates
- `indicesbot/backtest/`: synthetic smoke backtest that reuses live strategy code
- `indicesbot/daily_review.py`: bot-assessor-compatible review payload

## Required User Variables

OANDA:

```text
OANDA_ACCOUNT_ID=
OANDA_API_TOKEN=
OANDA_ENV=practice
```

Telegram:

```text
INDICES_TELEGRAM_TOKEN=
INDICES_TELEGRAM_CHAT_ID=
```

Safe defaults:

```text
EXECUTION_MODE=paper
PAPER_TRADE=true
LIVE_TRADING_ENABLED=false
INDICES_ENABLED_STRATEGIES=TREND_PULLBACK,OPENING_RANGE_BREAKOUT
INDICES_REQUIRE_CALIBRATION=true
```

`MEAN_REVERSION` and `EVENT_MOMENTUM` remain available in code, but they are disabled by default until they pass walk-forward validation on OANDA practice data. Paper/live entries also require a calibration payload with enough sample trades.

For live trading, all three must be set deliberately:

```text
EXECUTION_MODE=live
PAPER_TRADE=false
LIVE_TRADING_ENABLED=true
```

Live mode refuses to start unless OANDA and Telegram are configured.

## Telegram Messages

Messages are emoji-labelled and operator-focused, matching the commodities bot style. The bot sends clean alerts for:

- startup
- signal-only opportunities
- opened orders
- rejected or unfilled orders
- detected closures
- pause/resume actions
- runtime status

Startup alerts are de-duplicated across quick Railway restarts using `INDICES_STARTUP_MESSAGE_COOLDOWN_MINUTES` so a crash/restart loop does not flood Telegram with identical boot messages.

Supported commands:

```text
/help
/status
/open
/events
/pause
/resume
/sync
/closeall
```

Broker-affecting commands are queued into runtime state and handled by the runtime worker.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
python -m indicesbot.cli validate-config
```

Run once safely:

```powershell
$env:RUN_ONCE="true"
python main.py
```

Run the worker:

```powershell
indices-bot run
```

Refresh macro state:

```powershell
indices-bot macro
```

Run calibration/backtest:

```powershell
indices-bot calibrate
```

Paper and live workers require a usable calibration file by default. Run calibration first, or set `INDICES_REQUIRE_CALIBRATION=false` only for diagnostic signal checks.

Run a 30-day online-data backtest:

```powershell
$env:BACKTEST_OUTPUT_DIR="backtest_output\online_30d"
python -m indicesbot.backtest.run_backtest --days 30 --data-source online --granularity M15
```

`--data-source online` uses OANDA candles when OANDA credentials are configured and otherwise falls back to public Yahoo Finance index candles. The default `indices-bot calibrate` path remains synthetic so tests and local smoke runs do not require network access; it is not enough for paper/live entries under the default calibration gate.

Run a calibrated 30-day online backtest after the latest market-data pull:

```powershell
$env:BACKTEST_OUTPUT_DIR="backtest_output\online_30d_calibrated"
python -m indicesbot.backtest.run_backtest --days 30 --data-source online --granularity M15 --calibrate-first --min-score 78 --max-hold-bars 12
```

`--calibrate-first` runs an uncalibrated baseline pass on the loaded candles, applies the bot's existing symbol/strategy/direction calibration score adjustments, then writes the calibrated final summary. This is intended for research review before changing live environment variables.

## Railway

Use one repo with three services or commands:

- worker: `indices-bot run`
- macro: `indices-bot macro`
- calibration: `indices-bot calibrate`

Start in paper mode with OANDA practice credentials. Move to live only after Telegram, instrument discovery, unit precision, stop-loss placement, and order alerts are verified.
Run the calibration service before starting the worker, or keep the worker in `signal_only` until calibration is present.

## Tests

```powershell
pytest
```

The tests cover config parsing, OANDA order behavior, long/short strategy scoring, risk caps, Telegram message clarity, runtime paper flow, and backtest artifact writing.
