from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, *, path: Path, redis_url: str = "", redis_key: str = "") -> None:
        self.path = path
        self.redis_url = redis_url
        self.redis_key = redis_key

    def load(self) -> dict[str, Any]:
        payload: dict[str, Any] | None = self._load_redis()
        if payload is not None:
            return payload
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"schema_version": "1.0", "updated_at": utc_now_iso(), "paused": False, "halted": False, "open_positions": [], "events": []}

    def save(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True) if self.path.parent != Path(".") else None
        self.path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        self._save_redis(state)

    def publish_status(self, key: str, status: dict[str, Any], *, ttl_seconds: int = 1800) -> None:
        if not self.redis_url or not key:
            return
        try:
            import redis

            client = redis.from_url(self.redis_url, socket_connect_timeout=5, socket_timeout=5)
            client.set(key, json.dumps(status, default=str), ex=ttl_seconds)
        except Exception:
            return

    def _load_redis(self) -> dict[str, Any] | None:
        if not self.redis_url or not self.redis_key:
            return None
        try:
            import redis

            client = redis.from_url(self.redis_url, socket_connect_timeout=5, socket_timeout=5)
            raw = client.get(self.redis_key)
            if raw:
                return json.loads(raw)
        except Exception:
            return None
        return None

    def _save_redis(self, state: dict[str, Any]) -> None:
        if not self.redis_url or not self.redis_key:
            return
        try:
            import redis

            client = redis.from_url(self.redis_url, socket_connect_timeout=5, socket_timeout=5)
            client.set(self.redis_key, json.dumps(state, default=str), ex=7 * 24 * 3600)
        except Exception:
            return
