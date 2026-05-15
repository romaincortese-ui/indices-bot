from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from indicesbot.models import IndexPosition, Opportunity


SEPARATOR = "━━━━━━━━━━━━━━━"


def _safe(value: object, default: str = "n/a") -> str:
    text = "" if value is None else str(value).strip()
    return escape(text if text else default, quote=False)


def _humanize(value: object, default: str = "n/a") -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return default
    mapping = {
        "calibration_missing": "calibration file missing",
        "calibration_trades_below_min": "calibration sample too small",
        "calibration_groups_below_min": "not enough calibrated groups",
        "score_below_threshold": "score below threshold",
        "paused_or_halted": "paused or halted",
        "instrument_unavailable": "instrument unavailable",
        "pricing_unavailable": "pricing unavailable",
        "pricing_not_tradeable": "pricing not tradeable",
        "pricing_missing_bid_ask": "pricing missing bid/ask",
        "candles_unavailable": "waiting for candle history",
        "event_pause": "macro event pause",
        "spread_too_wide": "spread too wide",
        "order_not_filled": "order was not filled",
        "not_in_oanda_open_positions": "not in OANDA open positions",
        "max_hold_time_stop": "max hold time stop",
        "peak_pullback_profit_lock": "peak pullback profit lock",
    }
    if ":" in text:
        base, detail = text.split(":", 1)
        if base in mapping:
            detail = detail.replace("<", " < ").replace(">", " > ")
            return f"{mapping[base]} ({detail})"
    return mapping.get(text, text.replace("_", " "))


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


def _format_mode(mode: object) -> str:
    value = str(mode or "unknown").strip().lower()
    labels = {"live": "🔴 LIVE", "paper": "🧪 PAPER", "signal_only": "📡 SIGNAL_ONLY"}
    return labels.get(value, _safe(value.upper() if value else "UNKNOWN"))


def _format_account(account_label: object) -> str:
    value = str(account_label or "unknown").strip().lower()
    labels = {"live": "🔴 Live", "practice": "🧪 Practice"}
    return labels.get(value, _safe(value.title() if value else "Unknown"))


def _format_direction(direction: object) -> str:
    value = str(direction or "unknown").strip().upper()
    if value == "LONG":
        return "🟢 LONG"
    if value == "SHORT":
        return "🔴 SHORT"
    return f"⚪ {_safe(value)}"


def _format_status(status: object) -> str:
    value = str(status or "unknown").strip().lower()
    labels = {
        "running": "🟢 Running",
        "idle": "🟢 Idle",
        "blocked": "🟠 Blocked",
        "paused": "⏸️ Paused",
        "halted": "🛑 Halted",
        "signal_only": "📡 Signal only",
        "trade_opened": "🟢 Trade opened",
        "order_rejected": "🟠 Order rejected",
    }
    return labels.get(value, _safe(value.replace("_", " ").title()))


def _format_strategy(strategy: object) -> str:
    return _safe(str(strategy or "unknown").replace("_", " ").title())


def _format_symbols(symbols: tuple[str, ...]) -> list[str]:
    rows = []
    for index in range(0, len(symbols), 5):
        rows.append(", ".join(_safe(symbol) for symbol in symbols[index:index + 5]))
    return rows or ["n/a"]


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
        effective_parse_mode = parse_mode if parse_mode is not None else "HTML"
        payload = {"chat_id": self.chat_id, "text": text[:3500], "disable_web_page_preview": True}
        if effective_parse_mode:
            payload["parse_mode"] = effective_parse_mode
        try:
            urlopen(f"https://api.telegram.org/bot{self.token}/sendMessage?{urlencode(payload)}", timeout=10).read()
        except Exception:
            if not effective_parse_mode:
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


def startup_message(*, mode: str, universe: tuple[str, ...], account_label: str, calibration_required: bool | None = None) -> str:
    lines = [
        "🚀 <b>Indices Bot Online</b>",
        SEPARATOR,
        f"Mode: {_format_mode(mode)}",
        f"OANDA account: {_format_account(account_label)}",
        f"Universe: {len(universe)} indices",
        "Symbols:",
    ]
    lines.extend(f"• {row}" for row in _format_symbols(universe))
    if calibration_required is not None:
        lines.append(f"Calibration gate: {'🛡️ On' if calibration_required else '⚠️ Off'}")
    lines.append(f"Started: {_format_time(datetime.now(timezone.utc))}")
    return "\n".join(lines)


def status_message(state: dict[str, Any]) -> str:
    last_scan = state.get("last_scan", {}) if isinstance(state.get("last_scan"), dict) else {}
    positions = state.get("open_positions", []) if isinstance(state.get("open_positions"), list) else []
    if state.get("halted"):
        bot_state = "halted"
    elif state.get("paused"):
        bot_state = "paused"
    else:
        bot_state = "running"
    lines = [
        "📊 <b>Indices Status</b>",
        SEPARATOR,
        f"🤖 Bot: {_format_status(bot_state)}",
        f"📂 Open positions: {len(positions)}",
        f"🔎 Last scan: {_format_status(last_scan.get('status'))}",
        f"🚧 Last blocker: {_safe(_humanize(last_scan.get('blocked_by'), 'none'))}",
    ]
    focus = last_scan.get("symbol") or last_scan.get("best_opportunity")
    if focus:
        lines.append(f"🎯 Last focus: {_safe(focus)}")
    if state.get("signals_seen") is not None:
        lines.append(f"📡 Signals seen: {_safe(state.get('signals_seen'))}")
    lines.append(f"🕒 Updated: {_format_time(state.get('updated_at'))}")
    if positions:
        lines.append("")
        lines.append("📂 <b>Open positions</b>")
        for row in positions[:5]:
            if not isinstance(row, dict):
                continue
            target = _format_price(row.get("take_profit_price")) if row.get("take_profit_price") else "managed exit"
            lines.append(f"{_format_direction(row.get('direction'))} <b>{_safe(row.get('symbol', '?'))}</b> | {_format_strategy(row.get('strategy'))}")
            lines.append(f"Entry {_format_price(row.get('entry_price'))} | Stop {_format_price(row.get('stop_price'))} | Target {target}")
    return "\n".join(lines)


def opportunity_message(opportunity: Opportunity, *, mode: str) -> str:
    target = _format_price(opportunity.take_profit_price) if opportunity.take_profit_price else "managed exit"
    return "\n".join([
        f"🎯 <b>Indices Signal Ready</b> [{_format_mode(mode)}]",
        SEPARATOR,
        f"{_format_direction(opportunity.direction)} {_safe(opportunity.symbol)} | {_safe(opportunity.instrument)}",
        f"Strategy: {_format_strategy(opportunity.strategy)}",
        f"Score: {opportunity.score:.1f} | Risk/reward: {opportunity.risk_reward:.2f}",
        f"Entry: {_format_price(opportunity.entry_price)}",
        f"Stop: {_format_price(opportunity.stop_price)}",
        f"Target: {target}",
        f"Risk multiplier: {opportunity.risk_multiplier:.2f}",
        f"Why: {_safe(opportunity.rationale)}",
    ])


def order_opened_message(position: IndexPosition) -> str:
    target = _format_price(position.take_profit_price) if position.take_profit_price else "managed exit"
    return "\n".join([
        "🟢 <b>Indices Trade Opened</b>",
        SEPARATOR,
        f"{_format_direction(position.direction)} {_safe(position.symbol)} | {_safe(position.instrument)}",
        f"Strategy: {_format_strategy(position.strategy)}",
        f"Units: {position.units:.2f} | Order units: {position.order_units:.2f}",
        f"Entry: {_format_price(position.entry_price)}",
        f"Stop: {_format_price(position.stop_price)}",
        f"Target: {target}",
        f"Region: {_safe(position.region)}",
        f"Order ID: {_safe(position.order_id)}",
    ])


def order_rejected_message(*, symbol: str, direction: str, strategy: str, reason: str) -> str:
    return "\n".join([
        "🟠 <b>Indices Order Not Opened</b>",
        SEPARATOR,
        f"{_format_direction(direction)} {_safe(symbol)}",
        f"Strategy: {_format_strategy(strategy)}",
        f"Reason: {_safe(_humanize(reason))}",
    ])


def trade_closed_message(row: dict[str, Any], *, reason: str) -> str:
    return "\n".join([
        "⚪ <b>Indices Trade Closed</b>",
        SEPARATOR,
        f"{_format_direction(row.get('direction'))} {_safe(row.get('symbol', 'unknown'))} | {_safe(row.get('instrument', 'unknown'))}",
        f"Strategy: {_format_strategy(row.get('strategy'))}",
        f"Reason: {_safe(_humanize(reason))}",
        f"Order ID: {_safe(row.get('order_id', 'unknown'))}",
    ])


def profit_lock_message(row: dict[str, Any]) -> str:
    return "\n".join([
        "💰 <b>Indices Profit Taken</b>",
        SEPARATOR,
        f"{_format_direction(row.get('direction'))} {_safe(row.get('symbol', 'unknown'))} | {_safe(row.get('instrument', 'unknown'))}",
        f"P&L: {_format_percent(row.get('pnl_pct'))} | Peak: {_format_percent(row.get('peak_pnl_pct'))}",
        f"Pullback: {_format_points(row.get('pullback_from_peak_pct'))} pts",
        f"Order ID: {_safe(row.get('order_id', 'unknown'))}",
    ])


def help_message() -> str:
    return "\n".join([
        "🧭 <b>Indices Bot Commands</b>",
        SEPARATOR,
        "/status - Runtime, blockers, and open positions",
        "/open - Open index trades tracked by the bot",
        "/events - Cached high-impact index events",
        "/pause - Pause new entries; keep managing open trades",
        "/resume - Re-enable entries on the next scan",
        "/sync - Request broker reconciliation",
        "/closeall - Record a close-all request for operator follow-up",
        "/help - Show this help message",
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