from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

from indicesbot.config import IndicesConfig
from indicesbot.models import AccountSummary, Candle, IndexQuote, InstrumentDetails
from indicesbot.runtime import IndicesRuntime, _same_lane_stop_cooldown_reason


class Client:
    def account_summary(self):
        return AccountSummary(10000, 10000, 10000, 0, "USD")

    def instrument_tradeable(self, instrument):
        return True, "tradeable"

    def current_quote(self, symbol, instrument):
        return IndexQuote(symbol, instrument, 110, 110.1, 110.05, 0.1, True, "tradeable", datetime.now(timezone.utc))

    def candles(self, instrument, count=120, granularity="M15"):
        now = datetime.now(timezone.utc) - timedelta(hours=20)
        price = 100.0
        rows = []
        for index in range(120):
            open_price = price
            close = price + (0.15 if index < 118 else 1.2)
            rows.append(Candle(now + timedelta(minutes=15 * index), open_price, max(open_price, close) + 0.2, min(open_price, close) - 0.2, close, 1000))
            price = close
        return rows

    def instrument_details(self, instrument):
        return InstrumentDetails(instrument, margin_rate=0.02)

    def home_conversion_factor(self, instrument, direction):
        return 1.0

    def place_market_order(self, opportunity, units):
        return {"orderFillTransaction": {"id": "1", "price": str(opportunity.entry_price), "tradeOpened": {"tradeID": "T1"}}}


class ProtectionClient:
    def __init__(self, unrealized_pl: float) -> None:
        self.unrealized_pl = unrealized_pl
        self.closed_trades: list[str] = []

    def open_trades(self):
        return [
            {
                "id": "trade-1",
                "instrument": "SPX500_USD",
                "price": "5100.0",
                "currentUnits": "2",
                "initialMarginRequired": "100.0",
                "marginUsed": "100.0",
                "unrealizedPL": str(self.unrealized_pl),
            }
        ]

    def close_trade(self, trade_id):
        self.closed_trades.append(trade_id)
        return {"orderFillTransaction": {"tradeClosed": {"tradeID": trade_id}}}


class MarketDataErrorClient(Client):
    def instrument_tradeable(self, instrument):
        raise RuntimeError("OANDA request failed: {'errorMessage': 'Invalid Instrument BAD'}")


class SyncClient:
    def open_trades(self):
        return [{"id": "T2", "instrument": "NAS100_USD", "currentUnits": "0.01"}]

    def recent_trade_close(self, trade_id):
        if trade_id != "T1":
            return None
        return {
            "id": "99",
            "type": "ORDER_FILL",
            "reason": "STOP_LOSS_ORDER",
            "price": "5012.3",
            "time": "2026-05-22T13:46:10.000000000Z",
            "tradesClosed": [{"tradeID": "T1", "realizedPL": "-1.25", "financing": "-0.02", "halfSpreadCost": "0.01"}],
        }


class FailingSyncClient:
    def open_trades(self):
        raise ConnectionError("Remote end closed connection without response")


class Telegram:
    enabled = False

    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)

    def load_offset(self):
        return 0

    def save_offset(self, offset):
        pass

    def get_updates(self, offset, timeout=1):
        return []


def test_runtime_run_once_opens_paper_trade(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDICES_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_MACRO_STATE_FILE", str(tmp_path / "macro.json"))
    monkeypatch.setenv("INDICES_UNIVERSE", "SPX500")
    config = IndicesConfig.from_env()
    runtime = IndicesRuntime(config, client=Client(), telegram=Telegram())

    state = runtime.run_cycle()

    assert state["last_scan"]["status"] in {"trade_opened", "idle", "blocked"}
    assert (tmp_path / "review.json").exists()


def test_runtime_blocks_entries_without_calibration(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDICES_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_MACRO_STATE_FILE", str(tmp_path / "macro.json"))
    monkeypatch.setenv("INDICES_CALIBRATION_FILE", str(tmp_path / "missing_calibration.json"))
    monkeypatch.setenv("INDICES_UNIVERSE", "SPX500")
    config = IndicesConfig.from_env()
    runtime = IndicesRuntime(config, client=Client(), telegram=Telegram())

    state = runtime.run_cycle()

    assert state["last_scan"]["status"] == "blocked"
    assert state["last_scan"]["blocked_by"] == "calibration_missing"


def test_runtime_skips_symbol_on_market_data_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDICES_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_MACRO_STATE_FILE", str(tmp_path / "macro.json"))
    monkeypatch.setenv("INDICES_REQUIRE_CALIBRATION", "false")
    monkeypatch.setenv("INDICES_UNIVERSE", "SPX500")
    config = IndicesConfig.from_env()
    runtime = IndicesRuntime(config, client=MarketDataErrorClient(), telegram=Telegram())

    state = runtime.run_cycle()

    assert state["last_scan"]["status"] == "idle"
    assert any("SPX500:market_data_error:" in reason for reason in state["last_scan"]["reasons"])


def test_runtime_blocks_jp225_on_fx_macro_high_impact_event(tmp_path, monkeypatch) -> None:
    macro_path = tmp_path / "macro.json"
    macro_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "news_events": [
            {
                "currency": "JPY",
                "event": "BoJ policy statement",
                "impact": "High",
                "time": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            }
        ],
    }), encoding="utf-8")
    monkeypatch.setenv("INDICES_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_MACRO_STATE_FILE", str(macro_path))
    monkeypatch.setenv("INDICES_REQUIRE_CALIBRATION", "false")
    monkeypatch.setenv("INDICES_UNIVERSE", "JP225")
    config = IndicesConfig.from_env()
    runtime = IndicesRuntime(config, client=Client(), telegram=Telegram())

    state = runtime.run_cycle()

    assert state["last_scan"]["status"] == "idle"
    assert any("JP225:event_pause:BoJ policy statement" in reason for reason in state["last_scan"]["reasons"])


def test_startup_message_is_deduplicated(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDICES_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_MACRO_STATE_FILE", str(tmp_path / "macro.json"))
    monkeypatch.setenv("INDICES_STARTUP_MESSAGE_COOLDOWN_MINUTES", "30")
    config = IndicesConfig.from_env()
    telegram = Telegram()
    runtime = IndicesRuntime(config, client=Client(), telegram=telegram)
    state = runtime.state_store.load()

    runtime._announce_startup(state)
    runtime._announce_startup(runtime.state_store.load())

    assert len(telegram.messages) == 1
    assert "🚀 <b>Indices Bot Online</b>" in telegram.messages[0]
    saved = runtime.state_store.load()
    saved["last_startup_telegram_at"] = "2026-01-01T00:00:00+00:00"
    runtime._announce_startup(saved)

    assert len(telegram.messages) == 2


def _live_config(tmp_path, monkeypatch) -> IndicesConfig:
    monkeypatch.setenv("INDICES_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_MACRO_STATE_FILE", str(tmp_path / "macro.json"))
    return replace(
        IndicesConfig.from_env(),
        execution_mode="live",
        paper_trade=False,
        live_trading_enabled=True,
        oanda_account_id="acct",
        oanda_api_token="token",
        profit_lock_trigger_pct=15.0,
        profit_lock_pullback_pct=2.0,
    )


def test_profit_protection_closes_after_peak_pullback(tmp_path, monkeypatch) -> None:
    config = _live_config(tmp_path, monkeypatch)
    client = ProtectionClient(unrealized_pl=37.0)
    runtime = IndicesRuntime(config, client=client, telegram=Telegram())
    state = {
        "open_positions": [
            {
                "symbol": "SPX500",
                "instrument": "SPX500_USD",
                "direction": "LONG",
                "order_id": "trade-1",
                "metadata": {"peak_pnl_pct": 39.0},
            }
        ]
    }

    updates, errors = runtime._apply_profit_protection(state)

    assert errors == []
    assert len(updates) == 1
    assert client.closed_trades == ["trade-1"]
    assert state["open_positions"] == []
    assert updates[0]["peak_pnl_pct"] == 39.0
    assert updates[0]["pullback_from_peak_pct"] == 2.0


def test_profit_protection_records_new_peak_without_closing(tmp_path, monkeypatch) -> None:
    config = _live_config(tmp_path, monkeypatch)
    client = ProtectionClient(unrealized_pl=39.0)
    runtime = IndicesRuntime(config, client=client, telegram=Telegram())
    state = {
        "open_positions": [
            {
                "symbol": "SPX500",
                "instrument": "SPX500_USD",
                "direction": "LONG",
                "order_id": "trade-1",
                "metadata": {"peak_pnl_pct": 37.0},
            }
        ]
    }

    updates, errors = runtime._apply_profit_protection(state)

    assert errors == []
    assert updates == []
    assert client.closed_trades == []
    assert state["open_positions"][0]["metadata"]["peak_pnl_pct"] == 39.0


def test_no_progress_loss_exit_closes_aged_trade_without_mfe(tmp_path, monkeypatch) -> None:
    config = replace(
        _live_config(tmp_path, monkeypatch),
        no_progress_exit_enabled=True,
        no_progress_min_bars=4,
        no_progress_min_peak_r=0.10,
        no_progress_loss_r=0.35,
    )
    client = ProtectionClient(unrealized_pl=-40.0)
    runtime = IndicesRuntime(config, client=client, telegram=Telegram())
    now = datetime.now(timezone.utc)
    state = {
        "open_positions": [
            {
                "symbol": "SPX500",
                "instrument": "SPX500_USD",
                "direction": "LONG",
                "order_id": "trade-1",
                "opened_at": (now - timedelta(hours=3)).isoformat(),
                "metadata": {"risk_amount": 100.0, "no_progress_peak_r": 0.05},
            }
        ],
        "events": [],
    }

    updates, errors = runtime._apply_no_progress_loss_exit(state, now)

    assert errors == []
    assert len(updates) == 1
    assert client.closed_trades == ["trade-1"]
    assert state["open_positions"] == []
    assert updates[0]["exit_reason"] == "no_progress_loss_exit"
    assert updates[0]["no_progress_current_r"] == -0.4


def test_same_lane_stop_cooldown_blocks_recent_stopped_lane(tmp_path, monkeypatch) -> None:
    config = _live_config(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    opportunity = type("Opportunity", (), {"symbol": "NAS100", "direction": "SHORT", "strategy": "OPENING_RANGE_BREAKOUT"})()
    state = {
        "events": [
            {
                "type": "trade_closed",
                "symbol": "NAS100",
                "direction": "SHORT",
                "strategy": "OPENING_RANGE_BREAKOUT",
                "reason": "stop_loss_order",
                "at": (now - timedelta(minutes=20)).isoformat(),
            }
        ]
    }

    reason = _same_lane_stop_cooldown_reason(state, opportunity, now, config.same_lane_stop_cooldown_minutes)

    assert reason.startswith("same_lane_stop_cooldown:")


def test_time_stop_closes_aged_paper_position(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDICES_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_MACRO_STATE_FILE", str(tmp_path / "macro.json"))
    config = replace(IndicesConfig.from_env(), max_hold_bars=12, bar_minutes=15)
    runtime = IndicesRuntime(config, client=Client(), telegram=Telegram())
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=10)).isoformat()
    state = {"open_positions": [
        {"symbol": "SPX500", "instrument": "SPX500_USD", "order_id": "P1", "opened_at": old},
        {"symbol": "NAS100", "instrument": "NAS100_USD", "order_id": "P2", "opened_at": now.isoformat()},
    ]}

    stopped = runtime._apply_time_stop(state, now)

    assert [row["order_id"] for row in stopped] == ["P1"]
    assert [row["order_id"] for row in state["open_positions"]] == ["P2"]


def test_closed_position_sync_uses_trade_ids_and_enriches_close(tmp_path, monkeypatch) -> None:
    config = _live_config(tmp_path, monkeypatch)
    runtime = IndicesRuntime(config, client=SyncClient(), telegram=Telegram())
    state = {
        "open_positions": [
            {"symbol": "SPX500", "instrument": "SPX500_USD", "direction": "LONG", "order_id": "T1", "margin_used": 100.0},
            {"symbol": "NAS100", "instrument": "NAS100_USD", "direction": "LONG", "order_id": "T2", "margin_used": 150.0},
        ],
        "events": [],
    }

    closed = runtime._sync_closed_positions(state)

    assert [row["order_id"] for row in state["open_positions"]] == ["T2"]
    assert [row["order_id"] for row in closed] == ["T1"]
    assert closed[0]["exit_reason"] == "stop_loss_order"
    assert closed[0]["sync_reason"] == "not_in_oanda_open_positions"
    assert closed[0]["realized_pl"] == -1.25
    assert closed[0]["pnl_pct"] == -1.25
    assert state["events"][0]["reason"] == "stop_loss_order"


def test_closed_position_sync_does_not_crash_on_transient_oanda_disconnect(tmp_path, monkeypatch) -> None:
    config = _live_config(tmp_path, monkeypatch)
    runtime = IndicesRuntime(config, client=FailingSyncClient(), telegram=Telegram())
    state = {"open_positions": [{"symbol": "SPX500", "instrument": "SPX500_USD", "order_id": "T1"}]}

    closed = runtime._sync_closed_positions(state)

    assert closed == []
    assert state["open_positions"][0]["order_id"] == "T1"
    assert "Remote end closed connection" in state["last_closed_position_sync_error"]
