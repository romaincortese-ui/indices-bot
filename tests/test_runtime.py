from dataclasses import replace
from datetime import datetime, timedelta, timezone

from indicesbot.config import IndicesConfig
from indicesbot.models import AccountSummary, Candle, IndexQuote, InstrumentDetails
from indicesbot.runtime import IndicesRuntime


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
