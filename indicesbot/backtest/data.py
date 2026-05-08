from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from indicesbot.config import IndicesConfig
from indicesbot.models import Candle
from indicesbot.oanda_client import OandaClient


YAHOO_TICKERS = {
    "SPX500": "^GSPC",
    "NAS100": "^NDX",
    "US30": "^DJI",
    "UK100": "^FTSE",
    "DE40": "^GDAXI",
    "EU50": "^STOXX50E",
    "FR40": "^FCHI",
    "JP225": "^N225",
    "HK33": "^HSI",
    "AU200": "^AXJO",
}


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


def market_candles_by_symbol(config: IndicesConfig, *, days: int, source: str, granularity: str = "M15", symbols: tuple[str, ...] | None = None) -> tuple[dict[str, list[Candle]], dict[str, str]]:
    requested = symbols or config.universe
    normalized_source = source.lower().strip()
    if normalized_source == "online":
        normalized_source = "auto"
    candles_by_symbol: dict[str, list[Candle]] = {}
    sources: dict[str, str] = {}
    for index, symbol in enumerate(requested):
        candles: list[Candle] = []
        used_source = normalized_source
        if normalized_source == "synthetic":
            candles = synthetic_candles(days, start_price=100.0 + index * 50.0)
        elif normalized_source in {"auto", "oanda"}:
            candles = _oanda_candles(config, symbol, days=days, granularity=granularity)
            used_source = "oanda" if candles else ""
            if not candles and normalized_source == "oanda":
                raise RuntimeError(f"No OANDA candles available for {symbol}")
        if not candles and normalized_source in {"auto", "yahoo"}:
            candles = yahoo_candles(symbol, days=days, interval=_yahoo_interval(granularity))
            used_source = "yahoo" if candles else used_source
            if not candles and normalized_source == "yahoo":
                raise RuntimeError(f"No Yahoo candles available for {symbol}")
        if candles:
            candles_by_symbol[symbol] = candles
            sources[symbol] = used_source or normalized_source
    if not candles_by_symbol:
        raise RuntimeError(f"No candles loaded for source={source}")
    return candles_by_symbol, sources


def yahoo_candles(symbol: str, *, days: int = 30, interval: str = "15m") -> list[Candle]:
    ticker = YAHOO_TICKERS.get(symbol.upper(), symbol)
    params = urlencode({"range": f"{days}d", "interval": interval, "includePrePost": "false", "events": "history"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}?{params}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    payload = json.loads(urlopen(request, timeout=30).read().decode("utf-8"))
    chart = payload.get("chart", {}) if isinstance(payload, dict) else {}
    if chart.get("error"):
        raise RuntimeError(str(chart["error"]))
    results = chart.get("result", []) if isinstance(chart, dict) else []
    result = results[0] if results else {}
    timestamps = result.get("timestamp", []) if isinstance(result, dict) else []
    indicators = result.get("indicators", {}) if isinstance(result, dict) else {}
    quote_rows = indicators.get("quote", []) if isinstance(indicators, dict) else []
    quote_row = quote_rows[0] if quote_rows else {}
    candles: list[Candle] = []
    opens = quote_row.get("open", []) if isinstance(quote_row, dict) else []
    highs = quote_row.get("high", []) if isinstance(quote_row, dict) else []
    lows = quote_row.get("low", []) if isinstance(quote_row, dict) else []
    closes = quote_row.get("close", []) if isinstance(quote_row, dict) else []
    volumes = quote_row.get("volume", []) if isinstance(quote_row, dict) else []
    for index, timestamp in enumerate(timestamps):
        try:
            open_price = opens[index]
            high_price = highs[index]
            low_price = lows[index]
            close_price = closes[index]
        except IndexError:
            continue
        if None in {open_price, high_price, low_price, close_price}:
            continue
        volume = volumes[index] if index < len(volumes) and volumes[index] is not None else 0.0
        candles.append(Candle(datetime.fromtimestamp(int(timestamp), tz=timezone.utc), float(open_price), float(high_price), float(low_price), float(close_price), float(volume)))
    return candles


def _oanda_candles(config: IndicesConfig, symbol: str, *, days: int, granularity: str) -> list[Candle]:
    if not config.has_oanda_credentials:
        return []
    instrument = config.oanda_instrument_for(symbol)
    if not instrument:
        return []
    count = min(5000, max(120, days * _candles_per_day(granularity)))
    try:
        return OandaClient(config).candles(instrument, count=count, granularity=granularity)
    except Exception:
        return []


def _candles_per_day(granularity: str) -> int:
    normalized = granularity.upper()
    if normalized == "M5":
        return 288
    if normalized == "M15":
        return 96
    if normalized == "M30":
        return 48
    if normalized == "H1":
        return 24
    return 96


def _yahoo_interval(granularity: str) -> str:
    mapping = {"M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h"}
    return mapping.get(granularity.upper(), "15m")
