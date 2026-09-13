"""OAK warm-idle session state (shared JSON file for capture + ego_web)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _station_id() -> str:
    return os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001"


def warm_state_path() -> Path:
    explicit = os.environ.get("EGO_OAK_WARM_STATE", "").strip()
    if explicit:
        return Path(explicit)
    cache = os.environ.get(
        "EGO_CAPTURE_CHECKPOINT",
        f"/home/server/cache/{_station_id()}/checkpoint.json",
    )
    return Path(cache).parent / "oak_warm_state.json"


def ac_power_online() -> bool:
    root = Path("/sys/class/power_supply")
    if not root.is_dir():
        return True
    for child in root.iterdir():
        try:
            typ = (child / "type").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if typ not in ("Mains", "USB"):
            continue
        try:
            online = (child / "online").read_text(encoding="utf-8").strip()
        except OSError:
            return True
        if online == "1":
            return True
    return False


def warm_duration_s(*, ac_power_online_flag: bool | None = None) -> float:
    on_ac = ac_power_online() if ac_power_online_flag is None else ac_power_online_flag
    if not on_ac:
        return float(os.environ.get("EGO_PIPELINE_WARM_ON_BATTERY_S", "120"))
    return float(os.environ.get("EGO_OAK_WARM_S", os.environ.get("EGO_PIPELINE_WARM_S", "300")))


def read_warm_state() -> dict[str, Any]:
    path = warm_state_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_warm_state(*, active: bool, expires_at: float | None = None) -> None:
    path = warm_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "active": bool(active),
        "expires_at": expires_at,
        "updated_at": time.time(),
        "mode": "warm_idle",
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(path)


def clear_warm_state() -> None:
    path = warm_state_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def warm_state_active(now: float | None = None) -> bool:
    st = read_warm_state()
    if not st.get("active"):
        return False
    expires_at = st.get("expires_at")
    if expires_at is None:
        return True
    ts = time.time() if now is None else now
    try:
        return ts <= float(expires_at)
    except (TypeError, ValueError):
        return False


def warm_seconds_remaining(now: float | None = None) -> int:
    st = read_warm_state()
    if not st.get("active"):
        return 0
    expires_at = st.get("expires_at")
    if expires_at is None:
        return 0
    ts = time.time() if now is None else now
    try:
        return max(0, int(float(expires_at) - ts))
    except (TypeError, ValueError):
        return 0
