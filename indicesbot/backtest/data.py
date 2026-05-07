from __future__ import annotations

from datetime import datetime, timedelta, timezone

from indicesbot.models import Candle


def synthetic_candles(days: int = 30, *, start_price: float = 100.0) -> list[Candle]:
    candles: list[Candle] = []
    now = datetime.now(timezone.utc) - timedelta(days=days)
    price = start_price
    for index in range(max(120, days * 96)):
        drift = 0.08 if index % 180 < 100 else -0.06
        wave = ((index % 17) - 8) * 0.015
        open_price = price
        close = max(1.0, price + drift + wave)
        high = max(open_price, close) + 0.25
        low = min(open_price, close) - 0.25
        candles.append(Candle(now + timedelta(minutes=15 * index), open_price, high, low, close, 1000 + index % 50))
        price = close
    return candles
