from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from indicesbot.config import IndicesConfig
from indicesbot.news import default_macro_state


def refresh_macro_state(config: IndicesConfig) -> dict:
    payload = default_macro_state(source_status="manual_or_empty")
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    config.macro_state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if config.redis_url and config.macro_state_key:
        try:
            import redis

            redis.from_url(config.redis_url).set(config.macro_state_key, json.dumps(payload), ex=max(config.macro_refresh_seconds * 3, 900))
        except Exception:
            pass
    return payload


def load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
