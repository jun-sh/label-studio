"""CLI: upload closed local segments to Data Lab (run manually or via systemd timer)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ego_capture_studio.capture.camera_intrinsics import (
    INTRINSICS_STATUS_INVALID,
    is_intrinsics_valid,
    load_camera_intrinsics_json,
)
from ego_capture_studio.capture.segment_store import list_closed_pending_segments
from ego_capture_studio.capture.segment_upload import SegmentUploader, upload_pending_segments

try:
    from ego_capture_studio.capture.camera_map import ALL_LEROBOT_VIDEO_KEYS
except ImportError:
    from camera_map import ALL_LEROBOT_VIDEO_KEYS  # type: ignore[no-redef]


def main() -> None:
    p = argparse.ArgumentParser(description="Upload closed capture segments to Data Lab ingest.")
    p.add_argument(
        "--upload-url",
        type=str,
        required=True,
        help="http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/upload",
    )
    p.add_argument(
        "--segment-root",
        type=str,
        default=os.environ.get("EGO_SEGMENT_ROOT", "/home/server/cache/ego-lan-214/segments"),
    )
    p.add_argument("--session-id", type=str, default=os.environ.get("EGO_CAPTURE_SESSION_ID", ""))
    p.add_argument("--task", type=str, default="")
    p.add_argument("--limit", type=int, default=0, help="Max segments per run (0 = all pending)")
    p.add_argument("--ensure-session", action="store_true")
    args = p.parse_args()

    root = Path(args.segment_root)
    session_id = args.session_id.strip()
    if not session_id:
        reg_path = root / "registry.json"
        if reg_path.is_file():
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
            sessions = reg.get("sessions") or {}
            if sessions:
                session_id = sorted(
                    sessions.keys(),
                    key=lambda s: (sessions[s].get("updatedAt") or ""),
                )[-1]
        if not session_id:
            ck = root / "sessions"
            if ck.is_dir():
                subs = sorted([p.name for p in ck.iterdir() if p.is_dir()])
                if subs:
                    session_id = subs[-1]
    if not session_id:
        raise SystemExit("No session-id: pass --session-id or run capture first")

    checkpoint = root / "sessions" / session_id / "checkpoint.json"
    task = args.task
    if not task and checkpoint.is_file():
        raw = json.loads(checkpoint.read_text(encoding="utf-8"))
        task = raw.get("task") or ""

    pending = list_closed_pending_segments(root, session_id)
    print(f"session={session_id} pending_segments={len(pending)}", flush=True)
    if not pending:
        return

    uploader = SegmentUploader(args.upload_url)
    try:
        if args.ensure_session:
            shapes: dict[str, tuple[int, int]] = {}
            for key in ALL_LEROBOT_VIDEO_KEYS:
                shapes[key] = (800, 1280)
            intrinsics_path = root / "sessions" / session_id / "meta" / "camera_intrinsics.json"
            camera_intrinsics = None
            if intrinsics_path.is_file():
                camera_intrinsics = load_camera_intrinsics_json(intrinsics_path)
                if not is_intrinsics_valid(camera_intrinsics):
                    reasons = camera_intrinsics.get("invalid_reasons") or []
                    print(
                        f"WARNING: {INTRINSICS_STATUS_INVALID} session={session_id} "
                        f"reasons={reasons}; downstream hand pipeline must mark failed",
                        flush=True,
                    )
            uploader.start_session(
                session_id=session_id,
                task=task or None,
                video_shapes=shapes,
                camera_intrinsics=camera_intrinsics,
            )
        limit = args.limit if args.limit > 0 else None
        n = upload_pending_segments(
            root=root,
            session_id=session_id,
            uploader=uploader,
            limit=limit,
        )
        print(f"uploaded_segments={n}", flush=True)
    finally:
        uploader.close()


if __name__ == "__main__":
    main()
