"""Read OAK EEPROM and write camera_intrinsics.json (station cache + optional sessions)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ego_capture_studio.capture.oak_4p_capture import Oak4pEgoRecorder

from ego_capture_studio.capture.intrinsics_store import (
    backfill_session_intrinsics_if_missing,
    intrinsics_audit_line,
    require_valid_intrinsics,
    segment_root_from_env,
    write_session_intrinsics,
    write_station_intrinsics_cache,
)


def main() -> None:
    p = argparse.ArgumentParser(description="Dump OAK EEPROM intrinsics to session/station meta.")
    p.add_argument(
        "--segment-root",
        type=Path,
        default=None,
        help="EGO segment root (default: $EGO_SEGMENT_ROOT)",
    )
    p.add_argument(
        "--session-id",
        action="append",
        default=[],
        help="Backfill sessions/sess_xxx/meta/camera_intrinsics.json (repeatable)",
    )
    p.add_argument(
        "--allow-invalid",
        action="store_true",
        help="Write even when status is INTRINSICS_INVALID",
    )
    p.add_argument("--json", action="store_true", help="Print summary JSON to stdout")
    args = p.parse_args()

    root = Path(args.segment_root) if args.segment_root else segment_root_from_env()
    rec = Oak4pEgoRecorder(fps=30, device_fps=30, enable_imu=False)
    rec.connect()
    doc = rec.build_session_camera_intrinsics_document()

    if not args.allow_invalid:
        require_valid_intrinsics(doc)

    station_path = write_station_intrinsics_cache(doc, segment_root=root)
    session_paths: list[str] = []
    for sid in args.session_id:
        path = write_session_intrinsics(root, sid, doc)
        session_paths.append(str(path))
        print(intrinsics_audit_line(doc, path=path), flush=True)

    summary = {
        "valid": doc.get("status") == "valid",
        "device_mxid": doc.get("device_mxid"),
        "calibration_source": doc.get("calibration_source"),
        "station_path": str(station_path) if station_path else None,
        "session_paths": session_paths,
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(intrinsics_audit_line(doc, path=station_path), flush=True)
        if not session_paths:
            print("hint: pass --session-id sess_... to backfill session meta/", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
