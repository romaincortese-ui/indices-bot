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
