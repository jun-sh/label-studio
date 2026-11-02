"""In-flight abandon marker (ego_web writes, capture reads on warm-idle stop)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _station_id() -> str:
    import os

    return os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001"


def abandon_request_path() -> Path:
    import os

    explicit = os.environ.get("EGO_CAPTURE_ABANDON_REQUEST", "").strip()
    if explicit:
        return Path(explicit)
    cache = os.environ.get(
        "EGO_CAPTURE_CHECKPOINT",
        f"/home/server/cache/{_station_id()}/checkpoint.json",
    )
    return Path(cache).parent / "capture_abandon_request.json"


def read_abandon_request() -> dict[str, Any]:
    path = abandon_request_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_abandon_request(session_id: str) -> None:
    path = abandon_request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sessionId": session_id,
        "requested_at": time.time(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(path)


def clear_abandon_request() -> None:
    path = abandon_request_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def abandon_requested_for(session_id: str) -> bool:
    if not session_id:
        return False
    req = read_abandon_request()
    sid = req.get("sessionId")
    if not isinstance(sid, str) or not sid.strip():
        sid = req.get("session_id")
    if not isinstance(sid, str) or not sid.strip():
        return False
    return sid.strip() == session_id.strip()
