from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from indicesbot.models import NewsEvent


def parse_events(payload: dict[str, Any]) -> list[NewsEvent]:
    events: list[NewsEvent] = []
    for row in payload.get("events", []) if isinstance(payload.get("events", []), list) else []:
        if not isinstance(row, dict):
            continue
        try:
            events.append(
                NewsEvent(
                    id=str(row.get("id") or row.get("title") or "event"),
                    title=str(row.get("title") or "Untitled event"),
                    region=str(row.get("region") or "GLOBAL").upper(),
                    impact=str(row.get("impact") or "LOW").upper(),
                    occurs_at=datetime.fromisoformat(str(row.get("occurs_at")).replace("Z", "+00:00")),
                    actual=_maybe_float(row.get("actual")),
                    forecast=_maybe_float(row.get("forecast")),
                    previous=_maybe_float(row.get("previous")),
                    source=str(row.get("source") or "macro_state"),
                )
            )
        except (TypeError, ValueError):
            continue
    return events


def high_impact_event_block(region: str, events: list[NewsEvent], now: datetime, *, pre_minutes: int, post_minutes: int) -> str | None:
    for event in events:
        if event.region not in {region, "GLOBAL"} or event.impact != "HIGH":
            continue
        if event.occurs_at - timedelta(minutes=pre_minutes) <= now <= event.occurs_at:
            return f"event_pause:{event.title}"
        if event.occurs_at <= now <= event.occurs_at + timedelta(minutes=post_minutes):
            return f"post_event_settle:{event.title}"
    return None


def load_macro_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_macro_state()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_macro_state(source_status="invalid_cache")


def default_macro_state(*, source_status: str = "empty") -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "1.0",
        "generated_at": now,
        "source_status": {"calendar": source_status, "market_proxy": source_status},
        "events": [],
        "event_scores": [],
        "risk_regime": {"global": "MIXED", "vix_change_pct": 0.0, "us10y_change_bps": 0.0, "dxy_change_pct": 0.0},
        "region_bias": {},
    }


def write_default_macro_state(path: Path) -> dict[str, Any]:
    payload = default_macro_state()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _maybe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
