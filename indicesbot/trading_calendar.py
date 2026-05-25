from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo


US_INDEX_SYMBOLS = {"SPX500", "NAS100", "US30"}
NEW_YORK = ZoneInfo("America/New_York")


def opening_range_calendar_block(symbol: str, strategy: str, at: datetime, *, enabled: bool = True) -> str | None:
    if not enabled:
        return None
    if strategy.upper() != "OPENING_RANGE_BREAKOUT" or symbol.upper() not in US_INDEX_SYMBOLS:
        return None
    session_date = _new_york_date(at)
    if any(session_date in _us_equity_holidays(year) for year in (session_date.year - 1, session_date.year, session_date.year + 1)):
        return "us_market_holiday"
    if session_date in _us_equity_early_closes(session_date.year):
        return "us_market_early_close"
    return None


def _new_york_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(NEW_YORK).date()


@lru_cache(maxsize=16)
def _us_equity_holidays(year: int) -> frozenset[date]:
    days = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_date(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    return frozenset(days)


@lru_cache(maxsize=16)
def _us_equity_early_closes(year: int) -> frozenset[date]:
    candidates = {
        _nth_weekday(year, 11, 3, 4) + timedelta(days=1),
        date(year, 12, 24),
        date(year, 7, 3),
    }
    holidays = _us_equity_holidays(year)
    return frozenset(day for day in candidates if day.year == year and day.weekday() < 5 and day not in holidays)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    day = date(year, month, 1)
    offset = (weekday - day.weekday()) % 7
    return day + timedelta(days=offset + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        day = date(year, month + 1, 1) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def _easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)