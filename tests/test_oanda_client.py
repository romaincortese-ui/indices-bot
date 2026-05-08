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
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/instruments"):
            return Response(payload={"instruments": [{"name": "SPX500_USD", "displayPrecision": 1, "tradeUnitsPrecision": 0, "marginRate": "0.05"}]})
        if url.endswith("/orders"):
            return Response(payload={"orderFillTransaction": {"id": "10", "price": "5100.1", "tradeOpened": {"tradeID": "99"}}})
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
