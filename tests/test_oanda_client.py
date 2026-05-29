import json

from indicesbot.config import IndicesConfig
from indicesbot.models import Opportunity
from indicesbot.oanda_client import OandaClient


class Response:
    def __init__(self, status_code=200, payload=None, text="") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


class Session:
    def __init__(self, instruments=None) -> None:
        self.calls = []
        self.instruments = instruments or [{"name": "SPX500_USD", "displayPrecision": 1, "tradeUnitsPrecision": 0, "marginRate": "0.05"}]

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/instruments"):
            return Response(payload={"instruments": self.instruments})
        if url.endswith("/orders"):
            return Response(payload={"orderFillTransaction": {"id": "10", "price": "5100.1", "tradeOpened": {"tradeID": "99"}}})
        if "/trades?" in url and "state=CLOSED" in url:
            # Simulate the closed trades endpoint used by recent_trade_close primary path
            trade_id = url.split("ids=")[-1].split("&")[0] if "ids=" in url else ""
            if trade_id == "159":
                return Response(payload={"trades": [{"id": "159", "realizedPL": "-2.10", "closeTime": "2026-05-29T15:30:00.000000000Z", "averageClosePrice": "21450.0", "financing": "-0.0001"}]})
            return Response(payload={"trades": []})
        if "/transactions/sinceid" in url:
            # Fallback sinceid path — returns actual transaction objects
            return Response(payload={"transactions": [
                {"id": "158", "type": "ORDER_FILL", "tradesClosed": [{"tradeID": "158", "realizedPL": "1.20"}]},
                {"id": "159", "type": "ORDER_FILL", "reason": "STOP_LOSS_ORDER", "tradesClosed": [{"tradeID": "159", "realizedPL": "-2.10"}]},
            ]})
        return Response(payload={})


def test_live_order_uses_signed_units(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "acct")
    monkeypatch.setenv("OANDA_API_TOKEN", "token")
    monkeypatch.setenv("PAPER_TRADE", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    config = IndicesConfig.from_env()
    session = Session()
    client = OandaClient(config, session=session)
    opportunity = Opportunity("SPX500", "SPX500_USD", "SHORT", "TEST", 80, 5100, 5120, 5060, 15, 2, 1, "test", {})

    response = client.place_market_order(opportunity, -2)

    body = json.loads(session.calls[-1][2]["data"])
    assert body["order"]["units"] == "-2"
    assert body["order"]["stopLossOnFill"]["price"] == "5120"
    assert response["orderFillTransaction"]["tradeOpened"]["tradeID"] == "99"


def test_close_trade_requests_full_trade_close(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "acct")
    monkeypatch.setenv("OANDA_API_TOKEN", "token")
    monkeypatch.setenv("PAPER_TRADE", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    config = IndicesConfig.from_env()
    session = Session()
    client = OandaClient(config, session=session)

    client.close_trade("159")

    method, url, kwargs = session.calls[-1]
    body = json.loads(kwargs["data"])
    assert method == "PUT"
    assert url.endswith("/v3/accounts/acct/trades/159/close")
    assert body == {"units": "ALL"}


def test_live_order_formats_fractional_units(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "acct")
    monkeypatch.setenv("OANDA_API_TOKEN", "token")
    monkeypatch.setenv("PAPER_TRADE", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    config = IndicesConfig.from_env()
    session = Session(instruments=[{"name": "SPX500_USD", "displayPrecision": 1, "tradeUnitsPrecision": 2, "minimumTradeSize": "0.01", "marginRate": "0.05"}])
    client = OandaClient(config, session=session)
    opportunity = Opportunity("SPX500", "SPX500_USD", "LONG", "TEST", 80, 5100, 5080, 5140, 15, 2, 1, "test", {})

    client.place_market_order(opportunity, 0.01)

    details = client.instrument_details("SPX500_USD")
    body = json.loads(session.calls[-1][2]["data"])
    assert details.minimum_trade_size == 0.01
    assert body["order"]["units"] == "0.01"


def test_recent_trade_close_finds_matching_order_fill(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "acct")
    monkeypatch.setenv("OANDA_API_TOKEN", "token")
    monkeypatch.setenv("PAPER_TRADE", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    config = IndicesConfig.from_env()
    session = Session()
    client = OandaClient(config, session=session)

    close = client.recent_trade_close("159")

    # Primary path: normalised closed-trade dict with realizedPL in "pl"
    assert close is not None
    assert close["pl"] == "-2.10"
    assert close["time"] == "2026-05-29T15:30:00.000000000Z"
    assert "/trades?" in session.calls[-1][1]
    assert "state=CLOSED" in session.calls[-1][1]


def test_recent_trade_close_falls_back_to_sinceid(monkeypatch) -> None:
    """When the closed trades endpoint returns nothing, fall back to sinceid."""
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "acct")
    monkeypatch.setenv("OANDA_API_TOKEN", "token")
    monkeypatch.setenv("PAPER_TRADE", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    config = IndicesConfig.from_env()
    # Use a trade ID that is NOT in the closed trades mock so the fallback is exercised
    session = Session()
    client = OandaClient(config, session=session)

    close = client.recent_trade_close("158")

    # Fallback path: raw ORDER_FILL transaction dict from sinceid endpoint
    assert close is not None
    assert close["tradesClosed"][0]["realizedPL"] == "1.20"
    assert "/transactions/sinceid" in session.calls[-1][1]
