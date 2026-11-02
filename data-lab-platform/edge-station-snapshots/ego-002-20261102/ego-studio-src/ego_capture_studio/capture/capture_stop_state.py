"""Capture stop lifecycle — shared JSON contract for capture process and ego_web.

Phases (monotonic during stop):
  recording → stopping → flushing → sealing → sealed → warm_idle
On hard shutdown or new session start the file is cleared.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

PHASE_RECORDING = "recording"
PHASE_STOPPING = "stopping"
PHASE_FLUSHING = "flushing"
PHASE_SEALING = "sealing"
PHASE_SEALED = "sealed"
PHASE_WARM_IDLE = "warm_idle"
PHASE_ERROR = "error"

STOP_PHASES = (
    PHASE_RECORDING,
    PHASE_STOPPING,
    PHASE_FLUSHING,
    PHASE_SEALING,
    PHASE_SEALED,
    PHASE_WARM_IDLE,
    PHASE_ERROR,
)

# Phases where episode data is safely on disk (session_seal.json written).
SAFE_ON_DISK_PHASES = frozenset({PHASE_SEALED, PHASE_WARM_IDLE})

# Stop complete for ego_web (warm USB path).
STOP_COMPLETE_PHASES = frozenset({PHASE_SEALED, PHASE_WARM_IDLE})


def _station_id() -> str:
    return os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001"


def stop_state_path() -> Path:
    explicit = os.environ.get("EGO_CAPTURE_STOP_STATE", "").strip()
    if explicit:
        return Path(explicit)
    cache = os.environ.get(
        "EGO_CAPTURE_CHECKPOINT",
        f"/home/server/cache/{_station_id()}/checkpoint.json",
    )
    return Path(cache).parent / "capture_stop_state.json"


def read_stop_state() -> dict[str, Any]:
    path = stop_state_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_stop_state(
    phase: str,
    session_id: str,
    *,
    detail: str | None = None,
    error: str | None = None,
) -> None:
    path = stop_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "phase": phase,
        "session_id": session_id,
        "updated_at": time.time(),
    }
    if detail:
        payload["detail"] = detail
    if error:
        payload["error"] = error
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(path)


def clear_stop_state() -> None:
    path = stop_state_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def stop_phase() -> str:
    phase = read_stop_state().get("phase")
    return phase.strip() if isinstance(phase, str) and phase.strip() else ""


def stop_session_id() -> str:
    sid = read_stop_state().get("session_id")
    return sid.strip() if isinstance(sid, str) and sid.strip() else ""


def stop_is_complete() -> bool:
    return stop_phase() in STOP_COMPLETE_PHASES


def stop_data_safe_on_disk() -> bool:
    return stop_phase() in SAFE_ON_DISK_PHASES


def stop_error_message() -> str | None:
    err = read_stop_state().get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    return None
