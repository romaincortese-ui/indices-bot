from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from indicesbot.models import IndexPosition, Opportunity


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
        "Indices Bot started",
        f"Mode: {mode}",
        f"Account: {account_label}",
        f"Universe: {', '.join(universe)}",
        f"Started at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
    ])


def status_message(state: dict[str, Any]) -> str:
    last_scan = state.get("last_scan", {}) if isinstance(state.get("last_scan"), dict) else {}
    positions = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
    return "\n".join([
        "Indices Bot status",
        f"State: {'paused' if state.get('paused') else 'running'}",
        f"Open positions: {len(positions)}",
        f"Last scan: {last_scan.get('status', 'unknown')}",
        f"Last blocker: {last_scan.get('blocked_by', 'none')}",
        f"Updated: {state.get('updated_at', 'unknown')}",
    ])


def opportunity_message(opportunity: Opportunity, *, mode: str) -> str:
    target = f"{opportunity.take_profit_price:.2f}" if opportunity.take_profit_price else "managed"
    return "\n".join([
        "Indices signal ready",
        f"Mode: {mode}",
        f"Symbol: {opportunity.symbol}",
        f"Direction: {opportunity.direction}",
        f"Strategy: {opportunity.strategy}",
        f"Score: {opportunity.score:.1f}",
        f"Entry: {opportunity.entry_price:.2f}",
        f"Stop: {opportunity.stop_price:.2f}",
        f"Target: {target}",
        f"Reason: {opportunity.rationale}",
    ])


def order_opened_message(position: IndexPosition) -> str:
    target = f"{position.take_profit_price:.2f}" if position.take_profit_price else "managed"
    return "\n".join([
        "Indices order opened",
        f"Symbol: {position.symbol}",
        f"Direction: {position.direction}",
        f"Strategy: {position.strategy}",
        f"Units: {position.units:.2f}",
        f"Entry: {position.entry_price:.2f}",
        f"Stop: {position.stop_price:.2f}",
        f"Target: {target}",
        f"Order ID: {position.order_id}",
    ])


def order_rejected_message(*, symbol: str, direction: str, strategy: str, reason: str) -> str:
    return "\n".join([
        "Indices order was not opened",
        f"Symbol: {symbol}",
        f"Direction: {direction}",
        f"Strategy: {strategy}",
        f"Reason: {reason}",
    ])


def trade_closed_message(row: dict[str, Any], *, reason: str) -> str:
    return "\n".join([
        "Indices trade closed",
        f"Symbol: {row.get('symbol', 'unknown')}",
        f"Direction: {row.get('direction', 'unknown')}",
        f"Strategy: {row.get('strategy', 'unknown')}",
        f"Reason: {reason}",
        f"Order ID: {row.get('order_id', 'unknown')}",
    ])


def help_message() -> str:
    return "\n".join([
        "Indices Bot commands",
        "/status - bot, scan, and open position summary",
        "/open - list open positions",
        "/events - latest macro/news state",
        "/pause - stop opening new trades",
        "/resume - allow new trades again",
        "/sync - sync state with OANDA on next cycle",
        "/closeall - request closing all open trades on next cycle",
        "/help - show this help message",
    ])
