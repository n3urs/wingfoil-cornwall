"""Tiny on-disk JSON cache so the dashboard is instant on refresh and still
shows something useful when you're offline or the API is down."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _path(key: str) -> Path:
    return CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")


def get(key: str, max_age_s: float) -> Any | None:
    p = _path(key)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - blob.get("_at", 0) > max_age_s:
        return None
    return blob.get("data")


def get_stale(key: str) -> tuple[Any | None, float]:
    """Return cached data at any age, plus its age in seconds."""
    p = _path(key)
    if not p.exists():
        return None, 0.0
    try:
        blob = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None, 0.0
    return blob.get("data"), time.time() - blob.get("_at", 0)


def put(key: str, data: Any) -> None:
    _path(key).write_text(json.dumps({"_at": time.time(), "data": data}))
