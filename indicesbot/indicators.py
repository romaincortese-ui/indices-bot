from __future__ import annotations

from statistics import mean, pstdev

from indicesbot.models import Candle


def closes(candles: list[Candle]) -> list[float]:
    return [candle.close for candle in candles]


def sma(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    return mean(values[-period:]) if len(values) >= period else mean(values)


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    multiplier = 2.0 / (period + 1.0)
    current = values[0]
    for value in values[1:]:
        current = (value * multiplier) + (current * (1.0 - multiplier))
    return current


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        true_range = max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close))
        ranges.append(max(0.0, true_range))
        previous_close = candle.close
    return sma(ranges, period)


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        change = current - previous
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def bollinger(values: list[float], period: int = 20, deviations: float = 2.0) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    window = values[-period:]
    mid = mean(window)
    deviation = pstdev(window) if len(window) > 1 else 0.0
    return mid - deviations * deviation, mid, mid + deviations * deviation


def trend(candles: list[Candle], fast: int = 20, slow: int = 50) -> str:
    values = closes(candles)
    if len(values) < max(fast, slow):
        return "RANGE"
    fast_value = ema(values, fast)
    slow_value = ema(values, slow)
    if fast_value > slow_value:
        return "BULL"
    if fast_value < slow_value:
        return "BEAR"
    return "RANGE"


def body_atr_ratio(candles: list[Candle], atr_value: float) -> float:
    if not candles or atr_value <= 0:
        return 0.0
    last = candles[-1]
    return abs(last.close - last.open) / atr_value


def candle_direction(candle: Candle) -> str:
    if candle.close > candle.open:
        return "UP"
    if candle.close < candle.open:
        return "DOWN"
    return "FLAT"
