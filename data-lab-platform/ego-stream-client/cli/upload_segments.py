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
from ego_capture_studio.capture.segment_upload import (
    SegmentUploader,
    upload_pending_segments,
    upload_session_seal_finalize,
    upload_session_until_complete,
)

try:
    from ego_capture_studio.capture.camera_map import ALL_LEROBOT_VIDEO_KEYS
except ImportError:
    from camera_map import ALL_LEROBOT_VIDEO_KEYS  # type: ignore[no-redef]


def main() -> None:
    p = argparse.ArgumentParser(description="Upload closed capture segments to Data Lab ingest.")
    p.add_argument(
        "--upload-url",
        type=str,
        default=os.environ.get(
            "EGO_UPLOAD_URL",
            "http://10.10.10.34:8080/lerobot/api/collection/stations/"
            + (os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001")
            + "/upload",
        ),
    )
    p.add_argument(
        "--segment-root",
        type=str,
        default=os.environ.get(
            "EGO_SEGMENT_ROOT",
            f"/home/server/cache/{os.environ.get('EGO_STATION_ID', 'ego-001').strip() or 'ego-001'}/segments",
        ),
    )
    p.add_argument("--session-id", type=str, default=os.environ.get("EGO_CAPTURE_SESSION_ID", ""))
    p.add_argument("--task", type=str, default="")
    p.add_argument("--limit", type=int, default=0, help="Max segments per run (0 = all pending)")
    p.add_argument("--ensure-session", action="store_true")
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-upload closed segments even if manifest uploaded=true (e.g. after 34 reset)",
    )
    p.add_argument(
        "--until-complete",
        action="store_true",
        default=os.environ.get("EGO_UPLOAD_UNTIL_COMPLETE", "0").strip().lower()
        in ("1", "true", "yes"),
        help="Wait for finalize and upload all segments before session seal",
    )
    p.add_argument(
        "--no-until-complete",
        action="store_false",
        dest="until_complete",
        help="Single-pass upload (legacy)",
    )
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

    pending = list_closed_pending_segments(
        root,
        session_id,
        include_uploaded=args.force,
    )
    print(f"session={session_id} pending_segments={len(pending)} force={args.force}", flush=True)

    uploader = SegmentUploader(args.upload_url)
    try:
        if args.ensure_session:
            shapes: dict[str, tuple[int, int]] = {}
            for key in ALL_LEROBOT_VIDEO_KEYS:
                shapes[key] = (720, 1280)
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
        if args.until_complete:
            n = upload_session_until_complete(
                root=root,
                session_id=session_id,
                uploader=uploader,
                force=args.force,
            )
            print(f"uploaded_segments={n}", flush=True)
        elif pending:
            limit = args.limit if args.limit > 0 else None
            n = upload_pending_segments(
                root=root,
                session_id=session_id,
                uploader=uploader,
                limit=limit,
                include_uploaded=args.force,
                force=args.force,
            )
            print(f"uploaded_segments={n}", flush=True)
        elif upload_session_seal_finalize(root=root, session_id=session_id, uploader=uploader):
            print("session_seal_uploaded=1", flush=True)
        else:
            print("uploaded_segments=0", flush=True)
    finally:
        uploader.close()


if __name__ == "__main__":
    main()
