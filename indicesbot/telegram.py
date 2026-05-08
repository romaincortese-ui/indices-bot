from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from indicesbot.models import IndexPosition, Opportunity


def _humanize(value: object, default: str = "n/a") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return text.replace("_", " ")


def _format_time(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value or "n/a")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _format_price(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


class TelegramClient:
    def __init__(self, *, token: str, chat_id: str, offset_file: Path | None = None) -> None:
        self.token = token
        self.chat_id = chat_id
        self.offset_file = offset_file or Path("telegram_state.json")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, *, parse_mode: str | None = None) -> None:
        if not self.enabled:
            return
        payload = {"chat_id": self.chat_id, "text": text[:3500], "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            urlopen(f"https://api.telegram.org/bot{self.token}/sendMessage?{urlencode(payload)}", timeout=10).read()
        except Exception:
            if not parse_mode:
                return
            payload.pop("parse_mode", None)
            payload["text"] = re.sub(r"<[^>]+>", "", text)[:3500]
            try:
                urlopen(f"https://api.telegram.org/bot{self.token}/sendMessage?{urlencode(payload)}", timeout=10).read()
            except Exception:
                return

    def get_updates(self, offset: int, timeout: int = 1) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            payload = json.loads(urlopen(f"https://api.telegram.org/bot{self.token}/getUpdates?{urlencode({'offset': offset, 'timeout': timeout})}", timeout=max(5, timeout + 3)).read().decode("utf-8"))
        except Exception:
            return []
        result = payload.get("result", []) if isinstance(payload, dict) else []
        return result if isinstance(result, list) else []

    def load_offset(self) -> int:
        if not self.offset_file.exists():
            return 0
        try:
            payload = json.loads(self.offset_file.read_text(encoding="utf-8"))
            return int(payload.get("offset", 0))
        except Exception:
            return 0

    def save_offset(self, offset: int) -> None:
        self.offset_file.write_text(json.dumps({"offset": offset}), encoding="utf-8")


def startup_message(*, mode: str, universe: tuple[str, ...], account_label: str) -> str:
    return "\n".join([
        "Indices Bot Boot",
        f"Mode: {mode.upper()} | Account: {account_label}",
        f"Universe: {len(universe)} indices",
        f"Symbols: {', '.join(universe)}",
        f"Started: {_format_time(datetime.now(timezone.utc))}",
    ])


def status_message(state: dict[str, Any]) -> str:
    last_scan = state.get("last_scan", {}) if isinstance(state.get("last_scan"), dict) else {}
    positions = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
    if state.get("halted"):
        bot_state = "Halted"
    elif state.get("paused"):
        bot_state = "Paused"
    else:
        bot_state = "Running"
    lines = [
        "Indices Status",
        f"Bot: {bot_state}",
        f"Open positions: {len(positions)}",
        f"Last scan: {_humanize(last_scan.get('status'), 'unknown')}",
        f"Last blocker: {_humanize(last_scan.get('blocked_by'), 'none')}",
    ]
    focus = last_scan.get("symbol") or last_scan.get("best_opportunity")
    if focus:
        lines.append(f"Last focus: {focus}")
    if state.get("signals_seen") is not None:
        lines.append(f"Signals seen: {state.get('signals_seen')}")
    lines.append(f"Updated: {_format_time(state.get('updated_at'))}")
    if positions:
        lines.append("")
        lines.append("Open positions")
        for row in positions[:5]:
            if not isinstance(row, dict):
                continue
            lines.append(f"{row.get('symbol', '?')} {row.get('direction', '?')} | {row.get('strategy', '?')}")
            lines.append(f"Entry {_format_price(row.get('entry_price'))} | Stop {_format_price(row.get('stop_price'))} | Target {_format_price(row.get('take_profit_price'))}")
    return "\n".join(lines)


def opportunity_message(opportunity: Opportunity, *, mode: str) -> str:
    target = _format_price(opportunity.take_profit_price) if opportunity.take_profit_price else "managed exit"
    return "\n".join([
        "Indices Signal Ready",
        f"Mode: {mode}",
        f"Symbol: {opportunity.symbol}",
        f"Instrument: {opportunity.instrument}",
        f"Direction: {opportunity.direction}",
        f"Strategy: {opportunity.strategy}",
        f"Score: {opportunity.score:.1f} | Risk/reward: {opportunity.risk_reward:.2f}",
        f"Entry: {_format_price(opportunity.entry_price)}",
        f"Stop: {_format_price(opportunity.stop_price)}",
        f"Target: {target}",
        f"Risk multiplier: {opportunity.risk_multiplier:.2f}",
        f"Reason: {opportunity.rationale}",
    ])


def order_opened_message(position: IndexPosition) -> str:
    target = _format_price(position.take_profit_price) if position.take_profit_price else "managed exit"
    return "\n".join([
        "Indices Trade Opened",
        f"Symbol: {position.symbol}",
        f"Instrument: {position.instrument}",
        f"Direction: {position.direction}",
        f"Strategy: {position.strategy}",
        f"Units: {position.units:.2f} | Order units: {position.order_units:.2f}",
        f"Entry: {_format_price(position.entry_price)}",
        f"Stop: {_format_price(position.stop_price)}",
        f"Target: {target}",
        f"Region: {position.region}",
        f"Order ID: {position.order_id}",
    ])


def order_rejected_message(*, symbol: str, direction: str, strategy: str, reason: str) -> str:
    return "\n".join([
        "Indices Order Not Opened",
        f"Symbol: {symbol}",
        f"Direction: {direction}",
        f"Strategy: {strategy}",
        f"Reason: {_humanize(reason)}",
    ])


def trade_closed_message(row: dict[str, Any], *, reason: str) -> str:
    return "\n".join([
        "Indices Trade Closed",
        f"Symbol: {row.get('symbol', 'unknown')}",
        f"Instrument: {row.get('instrument', 'unknown')}",
        f"Direction: {row.get('direction', 'unknown')}",
        f"Strategy: {row.get('strategy', 'unknown')}",
        f"Reason: {_humanize(reason)}",
        f"Order ID: {row.get('order_id', 'unknown')}",
    ])


def profit_lock_message(row: dict[str, Any]) -> str:
    return "\n".join([
        "Indices Profit Taken: Peak Pullback",
        f"Symbol: {row.get('symbol', 'unknown')}",
        f"Instrument: {row.get('instrument', 'unknown')}",
        f"Direction: {row.get('direction', 'unknown')}",
        f"P&L: {_format_percent(row.get('pnl_pct'))} | Peak: {_format_percent(row.get('peak_pnl_pct'))}",
        f"Pullback: {_format_points(row.get('pullback_from_peak_pct'))} pts",
        f"Order ID: {row.get('order_id', 'unknown')}",
    ])


def help_message() -> str:
    return "\n".join([
        "Indices Bot commands",
        "/status - runtime state, last scan, and open positions",
        "/open - positions currently tracked by runtime state",
        "/events - cached high-impact index events",
        "/pause - pause new entries; existing positions remain monitored",
        "/resume - allow new entries again",
        "/sync - record a broker sync request; closed-position sync runs each cycle",
        "/closeall - record a close-all request for operator follow-up",
        "/help - show this help message",
    ])


def _format_percent(value: object) -> str:
    try:
        amount = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "n/a"
    sign = "+" if amount >= 0 else ""
    return f"{sign}{amount:.2f}%"


def _format_points(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"
