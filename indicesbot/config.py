from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:  # pragma: no cover - optional local convenience
    find_dotenv = None
    load_dotenv = None

if load_dotenv is not None and find_dotenv is not None:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)

DEFAULT_UNIVERSE = ("SPX500", "NAS100", "US30", "UK100", "DE40", "EU50", "FR40", "JP225", "HK33", "AU200")
DEFAULT_ENABLED_STRATEGIES = ("TREND_PULLBACK", "OPENING_RANGE_BREAKOUT", "EVENT_MOMENTUM")
DEFAULT_DISABLED_STRATEGY_LANES = (
    # US30 ORB SHORT kept off — Dow is thin and unreliable for breakout shorts
    "US30:OPENING_RANGE_BREAKOUT:SHORT",
    # FR40 ORB LONG kept off — FR40 already top earner via trend pullback, avoid overloading the lane
    "FR40:OPENING_RANGE_BREAKOUT:LONG",
)

REGION_BUCKETS = {
    "SPX500": "US",
    "NAS100": "US",
    "US30": "US",
    "UK100": "UK",
    "DE40": "EUROPE",
    "EU50": "EUROPE",
    "FR40": "EUROPE",
    "JP225": "ASIA",
    "HK33": "ASIA",
    "AU200": "ASIA_PACIFIC",
}

OANDA_INSTRUMENTS = {
    "SPX500": "SPX500_USD",
    "NAS100": "NAS100_USD",
    "US30": "US30_USD",
    "UK100": "UK100_GBP",
    "DE40": "DE30_EUR",
    "EU50": "EU50_EUR",
    "FR40": "FR40_EUR",
    "JP225": "JP225_USD",
    "HK33": "HK33_HKD",
    "AU200": "AU200_AUD",
}


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    return default if value == "" else int(value)


def env_float(name: str, default: float) -> float:
    value = env_str(name)
    return default if value == "" else float(value)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean for {name}: {value}")


def env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = env_str(name)
    if not raw:
        return default
    values = tuple(part.strip().upper() for part in re.split(r"[,\s]+", raw) if part.strip())
    return values or default


def env_csv_with_clear(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = env_str(name)
    if not raw:
        return default
    values = tuple(part.strip().upper() for part in re.split(r"[,\s]+", raw) if part.strip())
    if len(values) == 1 and values[0] in {"NONE", "OFF", "FALSE", "0"}:
        return ()
    return values or default


@dataclass(frozen=True, slots=True)
class IndicesConfig:
    oanda_env: str
    oanda_account_id: str
    oanda_api_token: str
    execution_mode: str
    paper_trade: bool
    live_trading_enabled: bool
    paper_balance: float
    universe: tuple[str, ...]
    oanda_instruments: dict[str, str]
    scan_interval_seconds: int
    heartbeat_seconds: int
    run_once: bool
    state_file: Path
    macro_state_file: Path
    calibration_file: Path
    daily_review_file: Path
    missed_opportunities_file: Path
    telegram_token: str
    telegram_chat_id: str
    telegram_poll_seconds: int
    telegram_heartbeat_minutes: int
    telegram_offset_file: Path
    startup_message_cooldown_minutes: int
    redis_url: str
    runtime_state_key: str
    bot_status_key: str
    macro_state_key: str
    calibration_key: str
    daily_review_key: str
    overlay_key: str
    budget_allocation: float
    max_risk_per_trade: float
    max_total_indices_risk: float
    max_open_indices_trades: int
    max_open_per_region: int
    max_open_per_symbol: int
    max_live_orders_per_scan: int
    max_margin_per_entry_pct: float
    min_margin_per_entry_pct: float
    min_unit_floor_enabled: bool
    min_unit_floor_max_risk_nav_pct: float
    min_unit_floor_small_account_max_risk_nav_pct: float
    min_unit_floor_small_account_max_total_risk_nav_pct: float
    min_unit_floor_small_account_max_margin_nav_pct: float
    min_unit_floor_max_target_risk_multiple: float
    daily_loss_halt_pct: float
    rolling_dd_throttle_pct: float
    rolling_dd_halt_pct: float
    prediction_overlay_enabled: bool
    prediction_overlay_state_file: Path
    prediction_overlay_stale_seconds: int
    prediction_overlay_fallback_mode: str
    prediction_overlay_min_favourable_probability: float
    prediction_overlay_min_posterior: float
    prediction_overlay_event_given_success: float
    prediction_overlay_kelly_base_fraction: float
    prediction_overlay_max_size_multiplier: float
    prediction_overlay_score_scale: float
    min_score: float
    max_hold_bars: int
    bar_minutes: int
    enabled_strategies: tuple[str, ...]
    disabled_strategy_lanes: tuple[str, ...]
    us_holiday_orb_block_enabled: bool
    same_lane_stop_cooldown_minutes: int
    require_calibration_for_trading: bool
    calibration_min_trades: int
    calibration_min_groups: int
    profit_lock_enabled: bool
    profit_lock_trigger_pct: float
    profit_lock_pullback_pct: float
    no_progress_exit_enabled: bool
    no_progress_min_bars: int
    no_progress_min_peak_r: float
    no_progress_loss_r: float
    max_entry_spread_atr: float
    adaptive_spread_enabled: bool
    adaptive_spread_window_minutes: int
    adaptive_spread_multiplier: float
    adaptive_spread_min_samples: int
    news_enabled: bool
    macro_refresh_seconds: int
    macro_state_max_age_minutes: int
    pre_event_pause_minutes: int
    post_event_settle_minutes: int
    high_impact_window_minutes: int
    risk_off_filter_enabled: bool
    risk_on_enabled_strategies: tuple[str, ...]
    risk_off_aggressive_enabled: bool
    risk_off_aggressive_enabled_strategies: tuple[str, ...]
    risk_off_aggressive_min_score: float
    risk_off_aggressive_short_score_bonus: float
    risk_off_aggressive_long_score_penalty: float
    risk_off_aggressive_risk_multiplier: float
    risk_off_aggressive_max_entry_spread_atr: float
    risk_off_aggressive_profit_lock_trigger_pct: float
    risk_off_aggressive_profit_lock_pullback_pct: float
    risk_off_aggressive_vix_level: float
    risk_off_aggressive_vix_change_pct: float
    backtest_days: int
    backtest_granularity: str
    backtest_initial_balance: float
    backtest_output_dir: Path

    @classmethod
    def from_env(cls) -> "IndicesConfig":
        execution_mode = env_str("EXECUTION_MODE", "paper").lower()
        paper_trade = env_bool("PAPER_TRADE", execution_mode != "live")
        universe = env_csv("INDICES_UNIVERSE", DEFAULT_UNIVERSE)
        return cls(
            oanda_env=env_str("OANDA_ENV", "practice").lower(),
            oanda_account_id=env_str("OANDA_ACCOUNT_ID"),
            oanda_api_token=env_str("OANDA_API_TOKEN") or env_str("OANDA_API_KEY"),
            execution_mode=execution_mode,
            paper_trade=paper_trade,
            live_trading_enabled=env_bool("LIVE_TRADING_ENABLED", False),
            paper_balance=env_float("PAPER_BALANCE", 10000.0),
            universe=universe,
            oanda_instruments={symbol: env_str(f"OANDA_INSTRUMENT_{symbol}", OANDA_INSTRUMENTS.get(symbol, "")).upper() for symbol in universe},
            scan_interval_seconds=max(30, env_int("SCAN_INTERVAL_SECONDS", 300)),
            heartbeat_seconds=env_int("INDICES_HEARTBEAT_SECONDS", env_int("HEARTBEAT_SECONDS", 21600)),
            run_once=env_bool("RUN_ONCE", False),
            state_file=Path(env_str("INDICES_STATE_FILE", "runtime_state.json")),
            macro_state_file=Path(env_str("INDICES_MACRO_STATE_FILE", "indices_macro_state.json")),
            calibration_file=Path(env_str("INDICES_CALIBRATION_FILE", "calibration.json")),
            daily_review_file=Path(env_str("INDICES_DAILY_REVIEW_FILE", "daily_review.json")),
            missed_opportunities_file=Path(env_str("INDICES_MISSED_OPPORTUNITIES_FILE", "missed_opportunities.json")),
            telegram_token=env_str("INDICES_TELEGRAM_TOKEN") or env_str("TELEGRAM_TOKEN"),
            telegram_chat_id=env_str("INDICES_TELEGRAM_CHAT_ID") or env_str("TELEGRAM_CHAT_ID"),
            telegram_poll_seconds=max(1, env_int("INDICES_TELEGRAM_POLL_SECONDS", 5)),
            telegram_heartbeat_minutes=max(0, env_int("INDICES_TELEGRAM_HEARTBEAT_MINUTES", 360)),
            telegram_offset_file=Path(env_str("INDICES_TELEGRAM_OFFSET_FILE", "telegram_state.json")),
            startup_message_cooldown_minutes=max(0, env_int("INDICES_STARTUP_MESSAGE_COOLDOWN_MINUTES", 30)),
            redis_url=env_str("REDIS_URL"),
            runtime_state_key=env_str("INDICES_RUNTIME_STATE_KEY", "indices_runtime_state"),
            bot_status_key=env_str("INDICES_BOT_STATUS_KEY", "indices_bot_runtime_status"),
            macro_state_key=env_str("INDICES_MACRO_STATE_KEY", "indices_macro_state"),
            calibration_key=env_str("INDICES_CALIBRATION_KEY", "indices_calibration"),
            daily_review_key=env_str("INDICES_DAILY_REVIEW_KEY", "bot_assessor:indices:daily_review"),
            overlay_key=env_str("INDICES_OVERLAY_KEY", "bot_assessor:indices:overlays"),
            budget_allocation=min(1.0, max(0.0, env_float("INDICES_BUDGET_ALLOCATION", 1.0))),
            max_risk_per_trade=env_float("MAX_RISK_PER_TRADE", 0.0039),
            max_total_indices_risk=env_float("MAX_TOTAL_INDICES_RISK", 0.012),
            max_open_indices_trades=env_int("MAX_OPEN_INDICES_TRADES", 3),
            max_open_per_region=env_int("MAX_OPEN_PER_REGION", 2),
            max_open_per_symbol=env_int("MAX_OPEN_PER_SYMBOL", 1),
            max_live_orders_per_scan=env_int("MAX_LIVE_ORDERS_PER_SCAN", 1),
            max_margin_per_entry_pct=env_float("MAX_MARGIN_PER_ENTRY_PCT", 0.08),
            min_margin_per_entry_pct=env_float("MIN_MARGIN_PER_ENTRY_PCT", 0.02),
            min_unit_floor_enabled=env_bool("INDICES_MIN_UNIT_FLOOR_ENABLED", True),
            min_unit_floor_max_risk_nav_pct=env_float("INDICES_MIN_UNIT_FLOOR_MAX_RISK_NAV_PCT", 0.001),
            min_unit_floor_small_account_max_risk_nav_pct=env_float("INDICES_MIN_UNIT_FLOOR_SMALL_ACCOUNT_MAX_RISK_NAV_PCT", 0.06),
            min_unit_floor_small_account_max_total_risk_nav_pct=env_float("INDICES_MIN_UNIT_FLOOR_SMALL_ACCOUNT_MAX_TOTAL_RISK_NAV_PCT", 0.10),
            min_unit_floor_small_account_max_margin_nav_pct=env_float("INDICES_MIN_UNIT_FLOOR_SMALL_ACCOUNT_MAX_MARGIN_NAV_PCT", 0.60),
            min_unit_floor_max_target_risk_multiple=env_float("INDICES_MIN_UNIT_FLOOR_MAX_TARGET_RISK_MULTIPLE", 12.0),
            min_score=env_float("INDICES_MIN_SCORE", 78.0),
            max_hold_bars=env_int("INDICES_MAX_HOLD_BARS", 24),
            bar_minutes=env_int("INDICES_BAR_MINUTES", 15),
            enabled_strategies=env_csv("INDICES_ENABLED_STRATEGIES", DEFAULT_ENABLED_STRATEGIES),
            disabled_strategy_lanes=env_csv_with_clear("INDICES_DISABLED_STRATEGY_LANES", DEFAULT_DISABLED_STRATEGY_LANES),
            us_holiday_orb_block_enabled=env_bool("INDICES_US_HOLIDAY_ORB_BLOCK_ENABLED", True),
            same_lane_stop_cooldown_minutes=max(0, env_int("INDICES_SAME_LANE_STOP_COOLDOWN_MINUTES", 90)),
            require_calibration_for_trading=env_bool("INDICES_REQUIRE_CALIBRATION", True),
            calibration_min_trades=env_int("INDICES_CALIBRATION_MIN_TRADES", 30),
            calibration_min_groups=env_int("INDICES_CALIBRATION_MIN_GROUPS", 1),
            daily_loss_halt_pct=env_float("DAILY_LOSS_HALT_PCT", 0.015),
            rolling_dd_throttle_pct=env_float("ROLLING_DD_THROTTLE_PCT", 0.05),
            rolling_dd_halt_pct=env_float("ROLLING_DD_HALT_PCT", 0.10),
            prediction_overlay_enabled=env_bool("INDICES_PREDICTION_OVERLAY_ENABLED", False),
            prediction_overlay_state_file=Path(env_str("INDICES_BACKTEST_PREDICTION_STATE_FILE", env_str("INDICES_PREDICTION_STATE_FILE", ""))),
            prediction_overlay_stale_seconds=max(1, env_int("INDICES_PREDICTION_STALE_SECONDS", 60)),
            prediction_overlay_fallback_mode=env_str("INDICES_PREDICTION_FALLBACK_MODE", "neutral").lower(),
            prediction_overlay_min_favourable_probability=max(0.0, min(1.0, env_float("INDICES_PREDICTION_MIN_FAVOURABLE_PROBABILITY", 0.50))),
            prediction_overlay_min_posterior=max(0.0, min(1.0, env_float("INDICES_PREDICTION_MIN_POSTERIOR", 0.50))),
            prediction_overlay_event_given_success=max(0.0, min(1.0, env_float("INDICES_PREDICTION_EVENT_GIVEN_SUCCESS", 0.60))),
            prediction_overlay_kelly_base_fraction=max(0.001, env_float("INDICES_PREDICTION_KELLY_BASE_FRACTION", 0.04)),
            prediction_overlay_max_size_multiplier=max(0.0, env_float("INDICES_PREDICTION_MAX_SIZE_MULTIPLIER", 1.0)),
            prediction_overlay_score_scale=max(0.0, env_float("INDICES_PREDICTION_SCORE_SCALE", 20.0)),
            profit_lock_enabled=env_bool("PROFIT_LOCK_ENABLED", True),
            profit_lock_trigger_pct=env_float("PROFIT_LOCK_TRIGGER_PCT", 20.0),
            profit_lock_pullback_pct=env_float("PROFIT_LOCK_PULLBACK_PCT", 4.0),
            no_progress_exit_enabled=env_bool("INDICES_NO_PROGRESS_EXIT_ENABLED", True),
            no_progress_min_bars=env_int("INDICES_NO_PROGRESS_MIN_BARS", 8),
            no_progress_min_peak_r=env_float("INDICES_NO_PROGRESS_MIN_PEAK_R", 0.12),
            no_progress_loss_r=env_float("INDICES_NO_PROGRESS_LOSS_R", 0.35),
            max_entry_spread_atr=env_float("MAX_ENTRY_SPREAD_ATR", 0.08),
            adaptive_spread_enabled=env_bool("ADAPTIVE_SPREAD_ENABLED", True),
            adaptive_spread_window_minutes=env_int("ADAPTIVE_SPREAD_WINDOW_MINUTES", 30),
            adaptive_spread_multiplier=env_float("ADAPTIVE_SPREAD_MULTIPLIER", 1.8),
            adaptive_spread_min_samples=env_int("ADAPTIVE_SPREAD_MIN_SAMPLES", 6),
            news_enabled=env_bool("NEWS_ENABLED", True),
            macro_refresh_seconds=env_int("MACRO_REFRESH_SECONDS", 300),
            macro_state_max_age_minutes=env_int("MACRO_STATE_MAX_AGE_MINUTES", 30),
            pre_event_pause_minutes=env_int("PRE_EVENT_PAUSE_MINUTES", 20),
            post_event_settle_minutes=env_int("POST_EVENT_SETTLE_MINUTES", 15),
            high_impact_window_minutes=env_int("HIGH_IMPACT_WINDOW_MINUTES", 180),
            risk_off_filter_enabled=env_bool("RISK_OFF_FILTER_ENABLED", True),
            risk_on_enabled_strategies=env_csv("RISK_ON_ENABLED_STRATEGIES", ("OPENING_RANGE_BREAKOUT",)),
            risk_off_aggressive_enabled=env_bool("RISK_OFF_AGGRESSIVE_ENABLED", False),
            risk_off_aggressive_enabled_strategies=env_csv("RISK_OFF_AGGRESSIVE_ENABLED_STRATEGIES", ("EVENT_MOMENTUM", "OPENING_RANGE_BREAKOUT")),
            risk_off_aggressive_min_score=env_float("RISK_OFF_AGGRESSIVE_MIN_SCORE", 73.0),
            risk_off_aggressive_short_score_bonus=env_float("RISK_OFF_AGGRESSIVE_SHORT_SCORE_BONUS", 4.0),
            risk_off_aggressive_long_score_penalty=env_float("RISK_OFF_AGGRESSIVE_LONG_SCORE_PENALTY", 6.0),
            risk_off_aggressive_risk_multiplier=env_float("RISK_OFF_AGGRESSIVE_RISK_MULTIPLIER", 1.5),
            risk_off_aggressive_max_entry_spread_atr=env_float("RISK_OFF_AGGRESSIVE_MAX_ENTRY_SPREAD_ATR", 0.14),
            risk_off_aggressive_profit_lock_trigger_pct=env_float("RISK_OFF_AGGRESSIVE_PROFIT_LOCK_TRIGGER_PCT", 6.0),
            risk_off_aggressive_profit_lock_pullback_pct=env_float("RISK_OFF_AGGRESSIVE_PROFIT_LOCK_PULLBACK_PCT", 1.8),
            risk_off_aggressive_vix_level=env_float("RISK_OFF_AGGRESSIVE_VIX_LEVEL", 25.0),
            risk_off_aggressive_vix_change_pct=env_float("RISK_OFF_AGGRESSIVE_VIX_CHANGE_PCT", 8.0),
            backtest_days=env_int("BACKTEST_DAYS", 30),
            backtest_granularity=env_str("BACKTEST_GRANULARITY", "M15"),
            backtest_initial_balance=env_float("BACKTEST_INITIAL_BALANCE", 10000.0),
            backtest_output_dir=Path(env_str("BACKTEST_OUTPUT_DIR", "backtest_output")),
        )

    @property
    def oanda_base_url(self) -> str:
        return "https://api-fxtrade.oanda.com" if self.oanda_env == "live" else "https://api-fxpractice.oanda.com"

    @property
    def has_oanda_credentials(self) -> bool:
        return bool(self.oanda_account_id and self.oanda_api_token)

    def oanda_instrument_for(self, symbol: str) -> str:
        return self.oanda_instruments.get(symbol.upper(), "")

    def region_for(self, symbol: str) -> str:
        return REGION_BUCKETS.get(symbol.upper(), "OTHER")

    def validate_for_live(self) -> None:
        if self.execution_mode != "live":
            return
        if self.paper_trade or not self.live_trading_enabled:
            raise RuntimeError("Live mode requires PAPER_TRADE=false and LIVE_TRADING_ENABLED=true")
        if not self.has_oanda_credentials:
            raise RuntimeError("Live mode requires OANDA_ACCOUNT_ID and OANDA_API_TOKEN or OANDA_API_KEY")
        if not self.telegram_token or not self.telegram_chat_id:
            raise RuntimeError("Live mode requires INDICES_TELEGRAM_TOKEN and INDICES_TELEGRAM_CHAT_ID")


def load_config() -> IndicesConfig:
    config = IndicesConfig.from_env()
    config.validate_for_live()
    return config
