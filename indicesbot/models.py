from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    complete: bool = True


@dataclass(frozen=True, slots=True)
class InstrumentDetails:
    name: str
    display_precision: int = 5
    trade_units_precision: int = 0
    margin_rate: float = 0.05


@dataclass(frozen=True, slots=True)
class AccountSummary:
    balance: float
    nav: float
    margin_available: float
    margin_used: float
    currency: str


@dataclass(frozen=True, slots=True)
class IndexQuote:
    symbol: str
    instrument: str
    bid: float
    ask: float
    mid: float
    spread: float
    tradeable: bool
    status: str
    time: datetime


@dataclass(frozen=True, slots=True)
class NewsEvent:
    id: str
    title: str
    region: str
    impact: str
    occurs_at: datetime
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class EventScore:
    event_id: str
    region: str
    direction: str
    score: float
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class MarketRegime:
    symbol: str
    region: str
    trend: str
    volatility: str
    risk_mode: str
    session: str
    score_offset_long: float = 0.0
    score_offset_short: float = 0.0
    risk_multiplier: float = 1.0
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Opportunity:
    symbol: str
    instrument: str
    direction: str
    strategy: str
    score: float
    entry_price: float
    stop_price: float
    take_profit_price: float | None
    atr: float
    risk_reward: float
    risk_multiplier: float
    rationale: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IndexPosition:
    symbol: str
    instrument: str
    direction: str
    strategy: str
    units: float
    order_units: float
    entry_price: float
    stop_price: float
    take_profit_price: float | None
    opened_at: datetime
    region: str
    order_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
