"""Curated event windows (IPO listings, mega-cap earnings, ad-hoc catalysts).

Economic-calendar feeds never carry corporate events like the 2026-06-12
SpaceX listing, so the operator curates them via the EVENT_WINDOW_CALENDAR env
var (JSON list):

  [{"time": "2026-06-12T13:30:00Z", "region": "US", "title": "SpaceX IPO",
    "pre_minutes": 30, "post_minutes": 300}]

Semantics: PRE window (time - pre_minutes .. time) blocks all new entries
(spreads widen, direction is a coin flip). POST window (time .. time +
post_minutes) activates the EVENT_WINDOW breakout lane, which trades the
post-event opening range direction-agnostically. Pure helpers, no I/O.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True, slots=True)
class EventWindow:
    title: str
    region: str
    occurs_at: datetime
    pre_minutes: int = 30
    post_minutes: int = 240


def _parse_time(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_event_windows(raw: str | None = None) -> list[EventWindow]:
    raw = raw if raw is not None else os.environ.get("EVENT_WINDOW_CALENDAR", "")
    if not raw.strip():
        return []
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        return []
    windows: list[EventWindow] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        occurs_at = _parse_time(row.get("time", ""))
        if occurs_at is None:
            continue
        windows.append(
            EventWindow(
                title=str(row.get("title") or "event"),
                region=str(row.get("region") or "GLOBAL").upper(),
                occurs_at=occurs_at,
                pre_minutes=max(0, int(row.get("pre_minutes", 30) or 30)),
                post_minutes=max(0, int(row.get("post_minutes", 240) or 240)),
            )
        )
    return windows


def _region_match(window: EventWindow, region: str) -> bool:
    return window.region in {str(region).upper(), "GLOBAL"} or str(region).upper() == "GLOBAL"


def pre_block(now: datetime, region: str, windows: list[EventWindow] | None = None) -> EventWindow | None:
    """Event whose PRE window covers ``now`` for this region (block entries)."""
    for w in windows if windows is not None else load_event_windows():
        if _region_match(w, region) and w.occurs_at - timedelta(minutes=w.pre_minutes) <= now < w.occurs_at:
            return w
    return None


def active_post_window(now: datetime, region: str, windows: list[EventWindow] | None = None) -> EventWindow | None:
    """Event whose POST window covers ``now`` for this region (trade lane on)."""
    for w in windows if windows is not None else load_event_windows():
        if _region_match(w, region) and w.occurs_at <= now <= w.occurs_at + timedelta(minutes=w.post_minutes):
            return w
    return None
