"""Station + session camera intrinsics persistence (EgoVerse / depthai_eeprom_v1)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ego_capture_studio.capture.camera_intrinsics import (
    INTRINSICS_REL_PATH,
    INTRINSICS_STATUS_INVALID,
    is_intrinsics_valid,
    load_camera_intrinsics_json,
    write_camera_intrinsics_json,
)

STATION_META_DIRNAME = "station_meta"


def segment_root_from_env() -> Path:
    return Path(os.environ.get("EGO_SEGMENT_ROOT", "/home/server/cache/ego-lan-214/segments"))


def station_meta_dir(segment_root: Path | None = None) -> Path:
    return Path(segment_root or segment_root_from_env()) / STATION_META_DIRNAME


def station_intrinsics_path(device_mxid: str, *, segment_root: Path | None = None) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in str(device_mxid))
    return station_meta_dir(segment_root) / f"camera_intrinsics_{safe}.json"


def session_intrinsics_path(segment_root: Path, session_id: str) -> Path:
    return Path(segment_root) / "sessions" / session_id / INTRINSICS_REL_PATH


def write_station_intrinsics_cache(
    document: dict[str, Any],
    *,
    segment_root: Path | None = None,
) -> Path | None:
    mxid = str(document.get("device_mxid") or "").strip()
    if not mxid:
        return None
    path = station_intrinsics_path(mxid, segment_root=segment_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_camera_intrinsics_json(path, document)
    return path


def write_session_intrinsics(
    segment_root: Path,
    session_id: str,
    document: dict[str, Any],
    *,
    mirror_station_cache: bool = True,
) -> Path:
    """Persist EEPROM intrinsics under sessions/sess_xxx/meta/ (uploaded via session_start)."""
    path = session_intrinsics_path(segment_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_camera_intrinsics_json(path, document)
    if mirror_station_cache:
        write_station_intrinsics_cache(document, segment_root=segment_root)
    return path


def _latest_station_intrinsics(segment_root: Path) -> Path | None:
    meta = station_meta_dir(segment_root)
    if not meta.is_dir():
        return None
    candidates = sorted(meta.glob("camera_intrinsics_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def resolve_session_intrinsics(segment_root: Path, session_id: str) -> dict[str, Any] | None:
    """Session file first, then newest station cache for this device."""
    sess_path = session_intrinsics_path(segment_root, session_id)
    if sess_path.is_file():
        return load_camera_intrinsics_json(sess_path)
    latest = _latest_station_intrinsics(segment_root)
    if latest is not None:
        return load_camera_intrinsics_json(latest)
    return None


def backfill_session_intrinsics_if_missing(
    segment_root: Path,
    session_id: str,
    *,
    allow_station_cache: bool = True,
) -> tuple[dict[str, Any] | None, Path | None]:
    """
    Ensure sessions/sess_xxx/meta/camera_intrinsics.json exists.
    Returns (document, path_written).
    """
    sess_path = session_intrinsics_path(segment_root, session_id)
    if sess_path.is_file():
        doc = load_camera_intrinsics_json(sess_path)
        return doc, sess_path
    if not allow_station_cache:
        return None, None
    latest = _latest_station_intrinsics(segment_root)
    if latest is None:
        return None, None
    doc = load_camera_intrinsics_json(latest)
    write_camera_intrinsics_json(sess_path, doc)
    return doc, sess_path


def intrinsics_strict_required() -> bool:
    return os.environ.get("EGO_INTRINSICS_STRICT", "1").strip().lower() in ("1", "true", "yes")


def require_valid_intrinsics(document: dict[str, Any]) -> None:
    if is_intrinsics_valid(document):
        return
    reasons = document.get("invalid_reasons") or []
    raise RuntimeError(f"{INTRINSICS_STATUS_INVALID}: {reasons}")


def intrinsics_audit_line(document: dict[str, Any], *, path: Path | None = None) -> str:
    parts = [
        f"status={document.get('status')}",
        f"mxid={document.get('device_mxid')}",
        f"source={document.get('calibration_source')}",
    ]
    if path is not None:
        parts.append(f"path={path}")
    return " ".join(parts)
