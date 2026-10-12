#!/usr/bin/env python3
"""Low-priority standalone station heartbeat (decoupled from capture process)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HEARTBEAT_URL = os.environ.get(
    "DATALAB_HEARTBEAT_URL",
    "http://10.10.10.34:8080/lerobot/api/collection/stations/ego-001/upload",
)
CAPTURE_HOST = os.environ.get("DATALAB_CAPTURE_HOST", "10.10.10.130")
STATION_TOKEN = os.environ.get("STATION_UPLOAD_TOKEN", "")
INTERVAL_S = max(5.0, float(os.environ.get("DATALAB_HEARTBEAT_INTERVAL_S", "15")))
TIMEOUT_S = max(1.0, float(os.environ.get("DATALAB_HEARTBEAT_TIMEOUT_S", "3")))
NICE_LEVEL = int(os.environ.get("DATALAB_HEARTBEAT_NICE", "10"))
_DEFAULT_STATION = os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001"
CHECKPOINT_PATH = Path(
    os.environ.get(
        "EGO_CAPTURE_CHECKPOINT",
        f"/home/server/cache/{_DEFAULT_STATION}/segments/checkpoint.json",
    )
)


def _session_id() -> str:
    if CHECKPOINT_PATH.is_file():
        try:
            raw = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
            sid = raw.get("sessionId")
            if isinstance(sid, str) and sid.strip():
                return sid.strip()
        except (OSError, json.JSONDecodeError):
            pass
    return os.environ.get("EGO_CAPTURE_SESSION_ID", "").strip()


try:
    from ego_capture_studio.capture.capture_state import resolve_capture_state
except ImportError:
    from capture_state import resolve_capture_state  # type: ignore[no-redef]


def _post_once() -> None:
    capture_state = resolve_capture_state()
    body = json.dumps(
        {
            "action": "heartbeat",
            "host": CAPTURE_HOST,
            "sessionId": _session_id() or None,
            "captureState": capture_state,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if STATION_TOKEN:
        headers["X-Station-Token"] = STATION_TOKEN
    req = urllib.request.Request(HEARTBEAT_URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        resp.read()


def main() -> None:
    try:
        os.nice(NICE_LEVEL)
    except OSError:
        pass
    print(
        f"station_heartbeat start url={HEARTBEAT_URL} host={CAPTURE_HOST} "
        f"interval_s={INTERVAL_S} timeout_s={TIMEOUT_S} nice={NICE_LEVEL}",
        flush=True,
    )
    while True:
        try:
            _post_once()
            print("heartbeat_ok", flush=True)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            print(f"heartbeat_fail err={exc}", flush=True)
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
