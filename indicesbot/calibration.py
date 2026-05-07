from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_calibration(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def strategy_adjustment(calibration: dict[str, Any] | None, *, symbol: str, strategy: str, direction: str) -> tuple[float, float]:
    if not calibration:
        return 0.0, 1.0
    key = f"{strategy}:{symbol}:{direction}".upper()
    grouped = calibration.get("groups", {}) if isinstance(calibration.get("groups"), dict) else {}
    row = grouped.get(key) or grouped.get(strategy.upper()) or {}
    if not isinstance(row, dict):
        return 0.0, 1.0
    trades = int(row.get("trades", 0) or 0)
    profit_factor = float(row.get("profit_factor", 1.0) or 1.0)
    if trades < 5:
        return 0.0, 1.0
    if profit_factor >= 1.4:
        return 4.0, 1.05
    if profit_factor < 0.8:
        return -8.0, 0.5
    return 0.0, 1.0


def write_calibration(path: Path, summary: dict[str, Any]) -> dict[str, Any]:
    payload = {"schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(), **summary}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
