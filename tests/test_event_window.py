from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from indicesbot.event_window import active_post_window, load_event_windows, pre_block
from indicesbot.models import Candle, IndexQuote, MarketRegime
from indicesbot.strategies.event_window import score_event_window

T0 = datetime(2026, 6, 12, 13, 30, tzinfo=timezone.utc)
CAL = '[{"time": "2026-06-12T13:30:00Z", "region": "US", "title": "SpaceX IPO", "pre_minutes": 30, "post_minutes": 300}]'


def test_load_and_windows():
    w = load_event_windows(CAL)
    assert len(w) == 1 and w[0].region == "US"
    assert pre_block(T0 - timedelta(minutes=10), "US", w) is not None
    assert pre_block(T0 - timedelta(minutes=40), "US", w) is None
    assert active_post_window(T0 + timedelta(minutes=60), "US", w) is not None
    assert active_post_window(T0 + timedelta(minutes=301), "US", w) is None
    assert pre_block(T0 - timedelta(minutes=10), "EU", w) is None  # region scoped


def _candle(t, h, l):
    mid = (h + l) / 2
    return Candle(time=t, open=mid, high=h, low=l, close=mid, volume=100.0, complete=True)


def _setup(monkeypatch, quote_bid, quote_ask):
    monkeypatch.setenv("EVENT_WINDOW_CALENDAR", CAL)
    candles = [_candle(T0 - timedelta(minutes=15 * (5 - i)), 5005.0, 4995.0) for i in range(5)]
    candles += [
        _candle(T0, 5010.0, 4990.0),
        _candle(T0 + timedelta(minutes=15), 5012.0, 4992.0),
        _candle(T0 + timedelta(minutes=30), 5015.0, 4995.0),
    ]
    quote = SimpleNamespace(bid=quote_bid, ask=quote_ask, spread=quote_ask - quote_bid, time=T0 + timedelta(minutes=45), tradeable=True)
    regime = SimpleNamespace(region="US")
    return quote, candles, regime


def test_breakout_long(monkeypatch):
    quote, candles, regime = _setup(monkeypatch, 5013.5, 5014.0)  # above range_high 5012
    opp = score_event_window(None, "SPX500", "SPX500_USD", "LONG", quote, candles, [], [], regime, {}, [])
    assert opp is not None and opp.direction == "LONG" and opp.metadata["range_high"] == 5012.0


def test_inside_range_no_trade(monkeypatch):
    quote, candles, regime = _setup(monkeypatch, 5000.0, 5000.5)
    reasons = []
    assert score_event_window(None, "SPX500", "SPX500_USD", "LONG", quote, candles, [], [], regime, {}, reasons) is None
    assert any("no_breakout" in r for r in reasons)


def test_inactive_outside_window(monkeypatch):
    quote, candles, regime = _setup(monkeypatch, 5013.5, 5014.0)
    quote.time = T0 + timedelta(hours=10)
    reasons = []
    assert score_event_window(None, "SPX500", "SPX500_USD", "LONG", quote, candles, [], [], regime, {}, reasons) is None
    assert any("no_active_event_window" in r for r in reasons)
