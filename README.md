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
```

For live trading, all three must be set deliberately:

```text
EXECUTION_MODE=live
PAPER_TRADE=false
LIVE_TRADING_ENABLED=true
```

Live mode refuses to start unless OANDA and Telegram are configured.

## Telegram Messages

Messages are intentionally plain and operator-focused. The bot sends clean alerts for:

- startup
- signal-only opportunities
- opened orders
- rejected or unfilled orders
- detected closures
- pause/resume actions
- runtime status

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

## Railway

Use one repo with three services or commands:

- worker: `indices-bot run`
- macro: `indices-bot macro`
- calibration: `indices-bot calibrate`

Start in paper mode with OANDA practice credentials. Move to live only after Telegram, instrument discovery, unit precision, stop-loss placement, and order alerts are verified.

## Tests

```powershell
pytest
```

The tests cover config parsing, OANDA order behavior, long/short strategy scoring, risk caps, Telegram message clarity, runtime paper flow, and backtest artifact writing.
