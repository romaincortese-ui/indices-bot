from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median


@dataclass(frozen=True, slots=True)
class SpreadDecision:
    ok: bool
    spread: float
    cap: float
    reason: str


class SpreadTracker:
    def __init__(self, *, window_minutes: int, multiplier: float, min_samples: int) -> None:
        self.window = timedelta(minutes=max(1, window_minutes))
        self.multiplier = max(1.0, multiplier)
        self.min_samples = max(1, min_samples)
        self.samples: dict[str, deque[tuple[datetime, float]]] = defaultdict(deque)

    def add(self, symbol: str, spread: float, *, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        rows = self.samples[symbol.upper()]
        rows.append((current, max(0.0, float(spread))))
        self._prune(rows, current)

    def cap(self, symbol: str, *, static_cap: float, atr_cap: float, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        rows = self.samples[symbol.upper()]
        self._prune(rows, current)
        caps = [cap for cap in (static_cap, atr_cap) if cap > 0]
        fallback = min(caps) if caps else max(static_cap, atr_cap, 0.0)
        if len(rows) < self.min_samples:
            return fallback
        dynamic = median([spread for _, spread in rows]) * self.multiplier
        if fallback > 0:
            return min(max(dynamic, 0.0), fallback)
        return max(dynamic, 0.0)

    def evaluate(self, symbol: str, spread: float, *, static_cap: float, atr_cap: float, now: datetime | None = None) -> SpreadDecision:
        cap = self.cap(symbol, static_cap=static_cap, atr_cap=atr_cap, now=now)
        ok = cap <= 0 or spread <= cap
        return SpreadDecision(ok=ok, spread=spread, cap=cap, reason="ok" if ok else "spread_too_wide")

    def _prune(self, rows: deque[tuple[datetime, float]], now: datetime) -> None:
        while rows and now - rows[0][0] > self.window:
            rows.popleft()
