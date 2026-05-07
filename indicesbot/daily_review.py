from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_daily_review(state: dict[str, Any], backtest_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    missed = state.get("missed_opportunities", []) if isinstance(state.get("missed_opportunities"), list) else []
    events = state.get("events", []) if isinstance(state.get("events"), list) else []
    blockers = Counter(str(row.get("blocked_by") or row.get("reason") or "unknown") for row in missed if isinstance(row, dict))
    orders_opened = sum(1 for row in events if isinstance(row, dict) and row.get("type") == "trade_opened")
    orders_closed = sum(1 for row in events if isinstance(row, dict) and row.get("type") == "trade_closed")
    return {
        "schema_version": "1.0",
        "bot_id": "indices",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "signals": int(state.get("signals_seen", 0) or 0),
            "orders_opened": orders_opened,
            "orders_closed": orders_closed,
            "missed_opportunities": len(missed),
            "blocked_by": dict(blockers),
        },
        "incidents": state.get("incidents", []),
        "missed_opportunities": missed[-50:],
        "recommendations": _recommendations(blockers, backtest_summary),
        "backtest": backtest_summary or {},
        "risk_flags": state.get("risk_flags", []),
    }


def write_daily_review(path: Path, state: dict[str, Any], backtest_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = build_daily_review(state, backtest_summary)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def _recommendations(blockers: Counter[str], backtest_summary: dict[str, Any] | None) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    if blockers.get("spread_too_wide", 0) >= 3:
        recommendations.append({"severity": "medium", "title": "Review spread settings", "action": "Check adaptive spread cap and OANDA index spreads by session."})
    if blockers.get("event_pause", 0) >= 3:
        recommendations.append({"severity": "low", "title": "Review event windows", "action": "Inspect missed opportunities around high-impact event gates."})
    if backtest_summary and float(backtest_summary.get("total_pnl", 0.0) or 0.0) < 0:
        recommendations.append({"severity": "high", "title": "Backtest is negative", "action": "Keep live mode disabled and review strategy calibration."})
    if not recommendations:
        recommendations.append({"severity": "info", "title": "No urgent action", "action": "Continue paper/live observation."})
    return recommendations
