from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from indicesbot.models import NewsEvent


log = logging.getLogger(__name__)

CURRENCY_REGIONS = {
    "USD": ("US",),
    "GBP": ("UK",),
    "EUR": ("EUROPE",),
    "CHF": ("EUROPE",),
    "JPY": ("ASIA",),
    "CNY": ("ASIA",),
    "CNH": ("ASIA",),
    "HKD": ("ASIA",),
    "AUD": ("ASIA_PACIFIC",),
    "NZD": ("ASIA_PACIFIC",),
}

HIGH_IMPACT_VALUES = {"HIGH", "RED", "3", "3/3", "3 OF 3", "HIGH IMPACT"}
MEDIUM_IMPACT_VALUES = {"MEDIUM", "ORANGE", "2", "2/3", "2 OF 3", "MEDIUM IMPACT"}


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


def load_macro_state(path: Path, *, redis_url: str = "", redis_key: str = "", max_age_minutes: int = 0) -> dict[str, Any]:
    redis_payload = _load_macro_state_from_redis(redis_url, redis_key)
    if redis_payload is not None:
        return normalize_macro_state(redis_payload, source_status="redis")
    if not path.exists():
        return default_macro_state()
    try:
        return normalize_macro_state(json.loads(path.read_text(encoding="utf-8")), source_status="file")
    except Exception:
        return default_macro_state(source_status="invalid_cache")


def _load_macro_state_from_redis(redis_url: str, redis_key: str) -> dict[str, Any] | None:
    if not redis_url or not redis_key:
        return None
    try:
        import redis

        client = redis.from_url(redis_url, socket_connect_timeout=5, socket_timeout=5)
        raw = client.get(redis_key)
    except Exception as exc:
        log.info("macro_state_redis_load_failed key=%s error=%s", redis_key, str(exc)[:160])
        return None
    if not raw:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
    except Exception as exc:
        log.info("macro_state_redis_invalid_json key=%s error=%s", redis_key, str(exc)[:160])
        return None
    return payload if isinstance(payload, dict) else None


def normalize_macro_state(payload: dict[str, Any], *, source_status: str = "cache") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return default_macro_state(source_status=source_status)
    base = default_macro_state(source_status=source_status)
    generated_at = payload.get("generated_at")
    if isinstance(generated_at, str) and generated_at.strip():
        base["generated_at"] = generated_at
    if isinstance(payload.get("source_status"), dict):
        base["source_status"] = payload["source_status"]
    if isinstance(payload.get("risk_regime"), dict):
        base["risk_regime"] = payload["risk_regime"]
    if isinstance(payload.get("region_bias"), dict):
        base["region_bias"] = payload["region_bias"]
    if isinstance(payload.get("filters"), dict):
        base["fx_filters"] = payload["filters"]

    events = _normalize_index_events(payload.get("events"))
    events.extend(_normalize_fx_news_events(payload.get("news_events")))
    base["events"] = events
    scores = payload.get("event_scores")
    base["event_scores"] = scores if isinstance(scores, list) else []
    return base


def _normalize_index_events(raw_events: object) -> list[dict[str, Any]]:
    if not isinstance(raw_events, list):
        return []
    events = []
    for row in raw_events:
        if isinstance(row, dict):
            events.append(dict(row))
    return events


def _normalize_fx_news_events(raw_events: object) -> list[dict[str, Any]]:
    if not isinstance(raw_events, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in raw_events:
        if not isinstance(row, dict):
            continue
        currency = str(row.get("currency") or "").strip().upper()
        regions = CURRENCY_REGIONS.get(currency, ("GLOBAL",))
        occurs_at = row.get("time") or row.get("occurs_at") or row.get("datetime")
        title = str(row.get("event") or row.get("title") or "Economic calendar event").strip()
        impact = _normalize_impact(row.get("impact"))
        for region in regions:
            normalized.append({
                "id": str(row.get("id") or f"{currency}:{title}:{occurs_at}"),
                "title": title,
                "region": region,
                "impact": impact,
                "occurs_at": occurs_at,
                "actual": row.get("actual"),
                "forecast": row.get("forecast"),
                "previous": row.get("previous"),
                "source": str(row.get("source") or "fx_macro_calendar"),
            })
    return normalized


def _normalize_impact(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in HIGH_IMPACT_VALUES:
        return "HIGH"
    if text in MEDIUM_IMPACT_VALUES:
        return "MEDIUM"
    return "LOW"


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
