# Indices Bot Workspace Instructions

- Keep this repository independent from the other trading bot repositories.
- Use safe defaults: paper or signal-only mode, never live by default.
- Do not hard-code OANDA, Telegram, Redis, or news-provider secrets.
- Keep broker-affecting actions inside the runtime worker.
- Telegram messages should be clear, concise, useful, and free of noisy formatting.
- Strategy logic must be pure and reused by backtests.
- All new behavior needs focused tests.

Checklist status:

- [x] Clarified project requirements
- [x] Scaffolded standalone Python project
- [x] Implemented indices trading core
- [x] Added OANDA, Telegram, state, risk, strategy, backtest, and review components
- [x] Added tests and documentation
- [x] Validated test suite and command smoke checks
- [x] Initialized git
