from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from indicesbot.config import IndicesConfig
from indicesbot.models import AccountSummary, Candle, IndexQuote, InstrumentDetails, Opportunity


TRANSIENT_STATUS_CODES = {429, 502, 503, 504}


class OandaClient:
    def __init__(self, config: IndicesConfig, *, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self._instrument_cache: dict[str, InstrumentDetails] | None = None
        self._currency_cache: str | None = None

    def tradeable_instruments(self) -> list[str]:
        return sorted(self._instrument_details().keys())

    def instrument_details(self, instrument: str) -> InstrumentDetails:
        return self._instrument_details().get(instrument.upper(), InstrumentDetails(instrument.upper()))

    def instrument_tradeable(self, instrument: str) -> tuple[bool, str]:
        if self.config.paper_trade or not self.config.has_oanda_credentials:
            return True, "paper"
        payload = self._request("GET", f"/v3/accounts/{self.config.oanda_account_id}/pricing?{urlencode({'instruments': instrument, 'includeHomeConversions': 'false'})}")
        prices = payload.get("prices", [])
        price = next((item for item in prices if isinstance(item, dict) and str(item.get("instrument", "")).upper() == instrument.upper()), None)
        if not isinstance(price, dict):
            return False, "pricing_unavailable"
        status = str(price.get("status") or "").lower()
        if status and status != "tradeable":
            return False, f"pricing_status_{status.replace('-', '_')}"
        if price.get("tradeable") is False:
            return False, "pricing_not_tradeable"
        if not price.get("bids") or not price.get("asks"):
            return False, "pricing_missing_bid_ask"
        return True, status or "tradeable"

    def account_summary(self) -> AccountSummary:
        if self.config.paper_trade or not self.config.has_oanda_credentials:
            return AccountSummary(self.config.paper_balance, self.config.paper_balance, self.config.paper_balance, 0.0, "USD")
        payload = self._request("GET", f"/v3/accounts/{self.config.oanda_account_id}/summary")
        account = payload.get("account", {}) if isinstance(payload, dict) else {}
        return AccountSummary(
            balance=float(account.get("balance", 0.0)),
            nav=float(account.get("NAV", account.get("balance", 0.0))),
            margin_available=float(account.get("marginAvailable", 0.0)),
            margin_used=float(account.get("marginUsed", 0.0)),
            currency=str(account.get("currency", "USD")).upper(),
        )

    def account_nav(self) -> float:
        return self.account_summary().nav

    def account_margin_available(self) -> float:
        return self.account_summary().margin_available

    def account_currency(self) -> str:
        if self._currency_cache:
            return self._currency_cache
        self._currency_cache = self.account_summary().currency
        return self._currency_cache

    def open_positions(self) -> set[str]:
        if self.config.paper_trade or not self.config.has_oanda_credentials:
            return set()
        payload = self._request("GET", f"/v3/accounts/{self.config.oanda_account_id}/openPositions")
        rows = payload.get("positions", []) if isinstance(payload, dict) else []
        result: set[str] = set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            long_units = abs(float((row.get("long") or {}).get("units") or 0.0))
            short_units = abs(float((row.get("short") or {}).get("units") or 0.0))
            if long_units > 0 or short_units > 0:
                result.add(str(row.get("instrument", "")).upper())
        return result

    def open_trades(self) -> list[dict[str, object]]:
        if self.config.paper_trade or not self.config.has_oanda_credentials:
            return []
        payload = self._request("GET", f"/v3/accounts/{self.config.oanda_account_id}/openTrades")
        trades = payload.get("trades", []) if isinstance(payload, dict) else []
        return trades if isinstance(trades, list) else []

    def current_quote(self, symbol: str, instrument: str) -> IndexQuote:
        if self.config.paper_trade or not self.config.has_oanda_credentials:
            candles = self.candles(instrument, count=2, granularity="M15")
            mid = candles[-1].close if candles else 100.0
            return IndexQuote(symbol, instrument, mid, mid, mid, 0.0, True, "paper", datetime.now(timezone.utc))
        payload = self._request("GET", f"/v3/accounts/{self.config.oanda_account_id}/pricing?{urlencode({'instruments': instrument, 'includeHomeConversions': 'true'})}")
        prices = payload.get("prices", []) if isinstance(payload, dict) else []
        price = next((item for item in prices if isinstance(item, dict) and str(item.get("instrument", "")).upper() == instrument.upper()), None)
        if not isinstance(price, dict):
            raise RuntimeError("pricing_unavailable")
        bid = float(price["bids"][0]["price"])
        ask = float(price["asks"][0]["price"])
        return IndexQuote(symbol, instrument, bid, ask, (bid + ask) / 2.0, max(0.0, ask - bid), price.get("tradeable") is not False, str(price.get("status") or "tradeable"), datetime.now(timezone.utc))

    def candles(self, instrument: str, count: int = 120, granularity: str = "M15", *, price: str = "M") -> list[Candle]:
        if not self.config.has_oanda_credentials:
            return []
        payload = self._request("GET", f"/v3/instruments/{instrument}/candles?{urlencode({'count': count, 'granularity': granularity, 'price': price})}")
        return _parse_candles(payload.get("candles", []) if isinstance(payload, dict) else [])

    def place_market_order(self, opportunity: Opportunity, units: float) -> dict[str, object]:
        if self.config.paper_trade:
            return {"paper": True, "orderFillTransaction": {"id": f"PAPER_{int(time.time() * 1000)}", "price": str(opportunity.entry_price), "tradeOpened": {"tradeID": f"PAPER_{int(time.time() * 1000)}"}}}
        signed_units = units if opportunity.direction.upper() == "LONG" else -abs(units)
        details = self.instrument_details(opportunity.instrument)
        order: dict[str, object] = {
            "type": "MARKET",
            "instrument": opportunity.instrument,
            "units": _format_units(signed_units, details.trade_units_precision),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "clientExtensions": {"tag": "indicesbot", "comment": f"{opportunity.symbol} {opportunity.direction} {opportunity.strategy} score={opportunity.score:.1f}"},
            "stopLossOnFill": {"price": _format_price(opportunity.stop_price, details.display_precision)},
        }
        if opportunity.take_profit_price:
            order["takeProfitOnFill"] = {"price": _format_price(opportunity.take_profit_price, details.display_precision)}
        return self._request("POST", f"/v3/accounts/{self.config.oanda_account_id}/orders", {"order": order})

    def close_trade(self, trade_id: str, units: str = "ALL") -> dict[str, object]:
        if self.config.paper_trade:
            return {"paper": True, "tradeID": trade_id, "units": units}
        return self._request("PUT", f"/v3/accounts/{self.config.oanda_account_id}/trades/{trade_id}/close", {"units": units})

    def recent_trade_close(self, trade_id: str, count: int = 100) -> dict[str, object] | None:
        if self.config.paper_trade or not self.config.has_oanda_credentials:
            return None
        target = str(trade_id or "").strip()
        if not target:
            return None
        # Primary: fetch the closed trade directly — returns realizedPL reliably.
        # Normalize to a shape that _enrich_row_from_close can consume via its
        # fallback path (it checks transaction.get("pl") when no tradesClosed parts).
        try:
            payload = self._request(
                "GET",
                f"/v3/accounts/{self.config.oanda_account_id}/trades?{urlencode({'ids': target, 'state': 'CLOSED'})}",
            )
            trades = payload.get("trades", []) if isinstance(payload, dict) else []
            for trade in trades if isinstance(trades, list) else []:
                if isinstance(trade, dict) and str(trade.get("id") or "") == target:
                    return {
                        "pl": trade.get("realizedPL"),
                        "time": trade.get("closeTime"),
                        "financing": trade.get("financing"),
                        "price": trade.get("averageClosePrice"),
                    }
        except Exception:
            pass
        # Fallback: walk transactions since the trade was opened via sinceid.
        # OANDA's /transactions?type=ORDER_FILL returns page links, not objects.
        # /transactions/sinceid always returns actual transaction dicts.
        try:
            since_id = max(1, int(target) - 1)
        except (ValueError, TypeError):
            since_id = 1
        payload = self._request(
            "GET",
            f"/v3/accounts/{self.config.oanda_account_id}/transactions/sinceid?{urlencode({'id': since_id})}",
        )
        transactions = payload.get("transactions", []) if isinstance(payload, dict) else []
        for transaction in reversed(transactions if isinstance(transactions, list) else []):
            if isinstance(transaction, dict) and target in _transaction_trade_ids(transaction):
                return transaction
        return None

    def home_conversion_factor(self, instrument: str, side: str) -> float:
        if self.config.paper_trade or not self.config.has_oanda_credentials:
            return 1.0
        try:
            payload = self._request(
                "GET",
                f"/v3/accounts/{self.config.oanda_account_id}/pricing?{urlencode({'instruments': instrument, 'includeHomeConversions': 'true'})}",
            )
        except Exception:
            return 1.0
        conversions = payload.get("homeConversions", []) if isinstance(payload, dict) else []
        if not isinstance(conversions, list) or not conversions:
            return 1.0
        # The instrument's quote currency conversion is the one whose pair matches the second half of the instrument
        # e.g. SPX500_USD -> USD; DE30_EUR -> EUR. Find it by `currency` field.
        quote_ccy = instrument.split("_")[-1].upper() if "_" in instrument else ""
        entry = None
        for item in conversions:
            if not isinstance(item, dict):
                continue
            if str(item.get("currency", "")).upper() == quote_ccy:
                entry = item
                break
        if entry is None:
            entry = next((item for item in conversions if isinstance(item, dict)), None)
        if not isinstance(entry, dict):
            return 1.0
        # For a LONG (positive PL in quote ccy), use positionValue. For a SHORT (negative PL), use negativePositionValue.
        key = "positionValue" if side.upper() == "LONG" else "negativePositionValue"
        try:
            factor = float(entry.get(key) or entry.get("positionValue") or 0.0)
        except (TypeError, ValueError):
            factor = 0.0
        return factor if factor > 0 else 1.0

    def _instrument_details(self) -> dict[str, InstrumentDetails]:
        if self._instrument_cache is not None:
            return self._instrument_cache
        if not self.config.has_oanda_credentials:
            self._instrument_cache = {}
            return self._instrument_cache
        payload = self._request("GET", f"/v3/accounts/{self.config.oanda_account_id}/instruments")
        rows = payload.get("instruments", []) if isinstance(payload, dict) else []
        self._instrument_cache = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            name = str(row["name"]).upper()
            trade_units_precision = _int(row.get("tradeUnitsPrecision"), 0)
            self._instrument_cache[name] = InstrumentDetails(
                name=name,
                display_precision=_int(row.get("displayPrecision"), 5),
                trade_units_precision=trade_units_precision,
                margin_rate=max(_float(row.get("marginRate"), 0.05), 0.0001),
                minimum_trade_size=max(_float(row.get("minimumTradeSize"), 10 ** -trade_units_precision if trade_units_precision > 0 else 1.0), 0.00000001),
            )
        return self._instrument_cache

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        if not self.config.has_oanda_credentials:
            raise RuntimeError("OANDA credentials are not configured")
        data = None if payload is None else json.dumps(payload)
        last_error = ""
        for attempt in range(3):
            response = self.session.request(
                method,
                self.config.oanda_base_url + path,
                data=data,
                headers={"Authorization": f"Bearer {self.config.oanda_api_token}", "Content-Type": "application/json"},
                timeout=20,
            )
            if response.status_code < 300:
                return response.json() if response.text else {}
            last_error = _extract_error(response)
            if response.status_code not in TRANSIENT_STATUS_CODES:
                break
            time.sleep(0.25 * (attempt + 1))
        raise RuntimeError(f"OANDA request failed: {last_error}")


def _parse_candles(rows: object) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        mid = row.get("mid") or row.get("bid") or row.get("ask")
        if not isinstance(mid, dict):
            continue
        try:
            complete = bool(row.get("complete", True))
            if not complete:
                continue
            candles.append(Candle(datetime.fromisoformat(str(row["time"]).replace("Z", "+00:00")), float(mid["o"]), float(mid["h"]), float(mid["l"]), float(mid["c"]), float(row.get("volume", 0.0)), complete))
        except (KeyError, TypeError, ValueError):
            continue
    return candles


def _extract_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        for key in ("orderRejectTransaction", "orderCancelTransaction"):
            item = payload.get(key)
            if isinstance(item, dict):
                return str(item.get("rejectReason") or item.get("reason") or payload)[:500]
        return str(payload)[:500]
    except Exception:
        return response.text[:500]


def _transaction_trade_ids(transaction: dict[str, object]) -> set[str]:
    ids: set[str] = set()
    for key in ("tradesClosed", "tradesReduced"):
        rows = transaction.get(key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("tradeID"):
                    ids.add(str(row["tradeID"]))
    for key in ("tradeClosed", "tradeReduced", "tradeOpened"):
        row = transaction.get(key)
        if isinstance(row, dict) and row.get("tradeID"):
            ids.add(str(row["tradeID"]))
    return ids


def _format_units(value: float, precision: int) -> str:
    digits = max(0, min(8, precision))
    if digits == 0:
        return str(int(round(value)))
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _format_price(value: float, precision: int) -> str:
    digits = max(0, min(8, precision))
    if digits == 0:
        return str(int(round(value)))
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
