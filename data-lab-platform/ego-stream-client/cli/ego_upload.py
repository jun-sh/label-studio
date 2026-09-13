"""One-shot upload: segments/ pending (Agent) or export/ready/*.tar.zst — with progress status file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ego_capture_studio.capture.camera_intrinsics import (
    INTRINSICS_STATUS_INVALID,
    is_intrinsics_valid,
    load_camera_intrinsics_json,
)
from ego_capture_studio.capture.intrinsics_store import backfill_session_intrinsics_if_missing
from ego_capture_studio.capture.segment_store import list_closed_pending_segments
from ego_capture_studio.capture.segment_upload import (
    SegmentUploader,
    _resolve_archive_session,
    _resolve_session_id,
    _session_from_export_ledger,
    find_latest_ready_dir,
    kick_derive_after_upload,
    list_ready_archives,
    upload_pending_segments,
    upload_ready_archives,
    upload_session_until_complete,
)
from ego_capture_studio.capture.upload_status import (
    LiveStatusPrinter,
    UploadStatusWriter,
    format_human_status,
    live_ui_enabled,
    read_status,
)

try:
    from ego_capture_studio.capture.camera_map import ALL_LEROBOT_VIDEO_KEYS
except ImportError:
    from camera_map import ALL_LEROBOT_VIDEO_KEYS  # type: ignore[no-redef]

DEFAULT_STATION = os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001"
DEFAULT_UPLOAD_URL = os.environ.get(
    "EGO_UPLOAD_URL",
    f"http://10.10.10.34:8080/lerobot/api/collection/stations/{DEFAULT_STATION}/upload",
)
DEFAULT_SEGMENT_ROOT = Path(
    os.environ.get("EGO_SEGMENT_ROOT", f"/home/server/cache/{DEFAULT_STATION}/segments")
)
DEFAULT_EXPORT_ROOT = Path(
    os.environ.get("EGO_EXPORT_ROOT", f"/home/server/export/{DEFAULT_STATION}")
)


def _ensure_session(uploader: SegmentUploader, root: Path, session_id: str, task: str) -> None:
    shapes: dict[str, tuple[int, int]] = {}
    for key in ALL_LEROBOT_VIDEO_KEYS:
        shapes[key] = (720, 1280)
    intrinsics_path = root / "sessions" / session_id / "meta" / "camera_intrinsics.json"
    camera_intrinsics = None
    if intrinsics_path.is_file():
        camera_intrinsics = load_camera_intrinsics_json(intrinsics_path)
    else:
        camera_intrinsics, backfilled = backfill_session_intrinsics_if_missing(root, session_id)
        if backfilled is not None:
            print(
                f"camera_intrinsics backfilled session={session_id} from_station_cache path={backfilled}",
                file=sys.stderr,
                flush=True,
            )
    if camera_intrinsics:
        if not is_intrinsics_valid(camera_intrinsics):
            reasons = camera_intrinsics.get("invalid_reasons") or []
            print(
                f"WARNING: {INTRINSICS_STATUS_INVALID} session={session_id} "
                f"reasons={reasons}",
                file=sys.stderr,
                flush=True,
            )
    uploader.start_session(
        session_id=session_id,
        task=task or None,
        video_shapes=shapes,
        camera_intrinsics=camera_intrinsics,
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description="Upload ego station segments to Data Lab (segments/ or export/ready/).",
    )
    p.add_argument(
        "--upload-url",
        default=DEFAULT_UPLOAD_URL,
        help="Ingest upload URL (…/stations/<station>/upload)",
    )
    p.add_argument("--segment-root", type=Path, default=DEFAULT_SEGMENT_ROOT)
    p.add_argument("--export-root", type=Path, default=DEFAULT_EXPORT_ROOT)
    p.add_argument("--session-id", default=os.environ.get("EGO_CAPTURE_SESSION_ID", ""))
    p.add_argument("--ready-dir", type=Path, default=None, help="export/ready/YYYYMMDD (auto if omitted)")
    p.add_argument(
        "--source",
        choices=("auto", "segments", "ready"),
        default="auto",
        help="auto: segments pending first, else latest ready/",
    )
    p.add_argument("--limit", type=int, default=0, help="Max segments per run (0 = all)")
    p.add_argument("--ensure-session", action="store_true", default=True)
    p.add_argument("--no-ensure-session", action="store_false", dest="ensure_session")
    p.add_argument(
        "--status-only",
        action="store_true",
        help="Only print current progress (no upload); same as ego-upload --status",
    )
    p.add_argument(
        "--no-live-ui",
        action="store_true",
        help="Disable in-terminal live progress panel",
    )
    p.add_argument(
        "--until-complete",
        action="store_true",
        default=os.environ.get("EGO_UPLOAD_UNTIL_COMPLETE", "0").strip().lower()
        in ("1", "true", "yes"),
        help="Wait for segment finalize and upload all CLOSED segments before session seal",
    )
    p.add_argument(
        "--no-until-complete",
        action="store_false",
        dest="until_complete",
        help="Single-pass upload (legacy); may leave multi-segment sessions incomplete",
    )
    args = p.parse_args()

    if args.status_only:
        print(format_human_status(read_status()))
        sys.exit(0 if read_status() else 1)

    if not args.no_live_ui and sys.stdout.isatty():
        os.environ["EGO_UPLOAD_LIVE_UI"] = "1"

    root = args.segment_root.resolve()
    session_id = _resolve_session_id(root, args.session_id)
    if not session_id:
        print("ERROR: no session-id; record first or pass --session-id", file=sys.stderr)
        raise SystemExit(2)

    pending = list_closed_pending_segments(root, session_id)
    ready_dir = args.ready_dir
    if ready_dir is None and args.source in ("auto", "ready"):
        ready_dir = find_latest_ready_dir(args.export_root.resolve())
    ready_archives = list_ready_archives(ready_dir) if ready_dir else []

    use_ready = False
    if args.source == "ready":
        use_ready = True
    elif args.source == "segments":
        use_ready = False
    else:
        use_ready = len(pending) == 0 and len(ready_archives) > 0

    uploader = SegmentUploader(args.upload_url)
    n = 0
    try:
        with LiveStatusPrinter():
            if use_ready:
                if not ready_dir or not ready_archives:
                    print("ERROR: no seg_*.tar.zst in export/ready/", file=sys.stderr)
                    raise SystemExit(2)
                sid = _session_from_export_ledger(ready_dir) or session_id
                limit = args.limit if args.limit > 0 else None
                batch = ready_archives[:limit] if limit else ready_archives
                status = UploadStatusWriter.get_default()
                status.reset_session(sid)
                status.refresh_queue(
                    session_id=sid,
                    pending=len(batch),
                    skipped_segments=[],
                    phase="uploading",
                )
                if args.ensure_session:
                    ensured: set[str] = set()
                    for archive_path in batch:
                        usid = _resolve_archive_session(ready_dir, archive_path, sid)
                        if usid in ensured:
                            continue
                        ensured.add(usid)
                        _ensure_session(uploader, root, usid, "")
                sessions_label = sid
                unique_sessions = {
                    _resolve_archive_session(ready_dir, ap, sid) for ap in batch
                }
                if len(unique_sessions) > 1:
                    sessions_label = f"multi({len(unique_sessions)})"
                print(
                    f"source=ready dir={ready_dir} archives={len(batch)} session={sessions_label}",
                    file=sys.stderr,
                    flush=True,
                )
                n = upload_ready_archives(
                    ready_dir=ready_dir,
                    session_id=sid,
                    uploader=uploader,
                    limit=limit,
                    skip_init=True,
                )
            else:
                if not pending and not args.until_complete:
                    print(
                        "nothing to upload in segments/; try --source ready or ego-export first",
                        file=sys.stderr,
                    )
                    raise SystemExit(0)
                if args.ensure_session:
                    checkpoint = root / "sessions" / session_id / "checkpoint.json"
                    task = ""
                    if checkpoint.is_file():
                        task = json.loads(checkpoint.read_text(encoding="utf-8")).get("task") or ""
                    _ensure_session(uploader, root, session_id, task)
                limit = args.limit if args.limit > 0 else None
                print(
                    f"source=segments pending={len(pending)} session={session_id} "
                    f"until_complete={args.until_complete}",
                    file=sys.stderr,
                    flush=True,
                )
                if args.until_complete:
                    n = upload_session_until_complete(
                        root=root,
                        session_id=session_id,
                        uploader=uploader,
                    )
                else:
                    n = upload_pending_segments(
                        root=root,
                        session_id=session_id,
                        uploader=uploader,
                        limit=limit,
                    )
        print(f"uploaded_segments={n}", file=sys.stderr, flush=True)
        if n > 0:
            kick = kick_derive_after_upload(uploader)
            if kick is None:
                print(
                    "✅ 上传完成（派生启动请求未确认，60s 内将自动兜底或请执行 ego-rederive --start-only）",
                    file=sys.stderr,
                    flush=True,
                )
            elif not live_ui_enabled():
                print(
                    "✅ 上传完成（{} 段），查询派生进度：ego-derive-status".format(n),
                    file=sys.stderr,
                    flush=True,
                )
        if not live_ui_enabled() or not sys.stdout.isatty():
            print(format_human_status(read_status()), flush=True)
    finally:
        uploader.close()


if __name__ == "__main__":
    main()
