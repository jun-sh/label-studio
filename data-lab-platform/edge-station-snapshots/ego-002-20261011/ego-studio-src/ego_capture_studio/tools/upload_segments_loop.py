"""Continuously upload closed segments to keep pending backlog below a target."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ego_capture_studio.capture.segment_store import (
    list_closed_pending_segments,
    mark_segment_uploaded,
    segment_store_bytes,
)
from ego_capture_studio.capture.upload_status import UploadStatusWriter, read_status, scan_skipped_segments

# Hard cap on closed-unuploaded segments (local queue depth, not archive).
KEEP_PENDING_BELOW = max(1, int(os.environ.get("EGO_UPLOAD_KEEP_PENDING_BELOW", "12")))
TRIM_BATCH = max(1, int(os.environ.get("EGO_UPLOAD_TRIM_BATCH", "3")))
PURGE_INTERVAL_S = float(os.environ.get("EGO_UPLOAD_PURGE_INTERVAL_S", "3600"))
POLL_INTERVAL_S = max(2.0, float(os.environ.get("EGO_UPLOAD_POLL_INTERVAL_S", "4")))
UPLOAD_BATCH = max(1, int(os.environ.get("EGO_UPLOAD_BATCH", "1")))
UPLOAD_TIMEOUT_S = max(30.0, float(os.environ.get("EGO_UPLOAD_SUBPROC_TIMEOUT_S", "300")))
FAIL_DEQUEUE = os.environ.get("EGO_UPLOAD_FAIL_DEQUEUE", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
UPLOAD_URL = os.environ.get(
    "EGO_UPLOAD_URL",
    "http://10.10.10.34:8080/lerobot/api/collection/stations/"
    + (os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001")
    + "/upload",
)
SEGMENT_ROOT = Path(
    os.environ.get(
        "EGO_SEGMENT_ROOT",
        f"/home/server/cache/{os.environ.get('EGO_STATION_ID', 'ego-001').strip() or 'ego-001'}/segments",
    )
)
QUOTA_BYTES = max(
    256 * 1024**2,
    int(float(os.environ.get("EGO_SEGMENT_QUOTA_GB", "256")) * 1024**3),
)


def _resolve_session_id(root: Path) -> str:
    env_sid = os.environ.get("EGO_CAPTURE_SESSION_ID", "").strip()
    if env_sid:
        return env_sid
    reg_path = root / "registry.json"
    if reg_path.is_file():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        sessions = {
            sid: meta
            for sid, meta in (reg.get("sessions") or {}).items()
            if sid and str(sid).startswith("sess_")
        }
        if sessions:
            return sorted(
                sessions.keys(),
                key=lambda s: (sessions[s].get("updatedAt") or ""),
            )[-1]
    ck = root / "sessions"
    if ck.is_dir():
        subs = sorted([p.name for p in ck.iterdir() if p.is_dir() and p.name.startswith("sess_")])
        if subs:
            return subs[-1]
    return ""


def _upload_once(session_id: str, *, limit: int) -> int:
    cmd = [
        sys.executable,
        "-m",
        "ego_capture_studio.cli.upload_segments",
        "--upload-url",
        UPLOAD_URL,
        "--segment-root",
        str(SEGMENT_ROOT),
        "--session-id",
        session_id,
        "--ensure-session",
        "--limit",
        str(limit),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=UPLOAD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(
            f"upload_timeout session={session_id} timeout_s={UPLOAD_TIMEOUT_S}",
            flush=True,
        )
        if FAIL_DEQUEUE:
            _drop_oldest_pending(session_id, reason="upload_timeout")
        return 0
    if proc.stdout:
        print(proc.stdout.strip(), flush=True)
    if proc.stderr:
        print(proc.stderr.strip(), file=sys.stderr, flush=True)
    uploaded = 0
    for line in (proc.stdout or "").splitlines():
        if line.startswith("uploaded_segments="):
            try:
                uploaded = int(line.split("=", 1)[1])
            except ValueError:
                pass
    if uploaded <= 0 and proc.returncode != 0 and FAIL_DEQUEUE:
        _drop_oldest_pending(session_id, reason="upload_fail")
    return uploaded


def _purge_uploaded_segments(session_id: str) -> int:
    """Remove uploaded segments from disk to keep directory scans cheap."""
    seg_root = SEGMENT_ROOT / "sessions" / session_id / "segments"
    if not seg_root.is_dir():
        return 0
    removed = 0
    for seg in sorted(seg_root.iterdir()):
        if not seg.is_dir():
            continue
        manifest_path = seg / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("uploaded"):
            shutil.rmtree(seg, ignore_errors=True)
            removed += 1
    return removed


def _drop_oldest_pending(session_id: str, *, reason: str) -> bool:
    pending = list_closed_pending_segments(SEGMENT_ROOT, session_id)
    if not pending:
        return False
    seg = pending[0]
    delete_after = os.environ.get("EGO_SEGMENT_DELETE_AFTER_UPLOAD", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    mark_segment_uploaded(seg, delete=delete_after)
    print(
        f"local_trim_drop session={session_id} segment={seg.name} reason={reason}",
        flush=True,
    )
    return True


def _trim_over_cap(session_id: str, pending: int) -> int:
    """Drop oldest segments when pending exceeds the hard cap."""
    if pending <= KEEP_PENDING_BELOW:
        return pending
    trim = min(pending - KEEP_PENDING_BELOW, TRIM_BATCH)
    for _ in range(trim):
        if not _drop_oldest_pending(session_id, reason="pending_cap"):
            break
    return max(0, pending - trim)


def _trim_for_disk_quota(session_id: str, pending: int) -> int:
    """Drop oldest segments when on-disk store exceeds quota."""
    trimmed = 0
    while pending > 1 and segment_store_bytes(SEGMENT_ROOT) > QUOTA_BYTES:
        if not _drop_oldest_pending(session_id, reason="disk_quota"):
            break
        pending -= 1
        trimmed += 1
    if trimmed:
        print(
            f"upload_loop disk_quota_trim session={session_id} dropped={trimmed} "
            f"bytes={segment_store_bytes(SEGMENT_ROOT)} quota={QUOTA_BYTES}",
            flush=True,
        )
    return pending


def _foreground_upload_active() -> bool:
    """True when manual ego-upload owns the status file (do not clobber from loop)."""
    st = read_status()
    if not st:
        return False
    if (st.get("service") or {}).get("phase") != "uploading":
        return False
    prog = st.get("progress") or {}
    pending = int(prog.get("pending", 0))
    return pending > 0 or bool(st.get("current"))


def main() -> None:
    writer = UploadStatusWriter.get_default()
    print(
        f"upload_loop start keep_pending_below={KEEP_PENDING_BELOW} "
        f"poll_s={POLL_INTERVAL_S} quota_gb={QUOTA_BYTES / 1024**3:.1f} root={SEGMENT_ROOT}",
        flush=True,
    )
    print(f"upload_status path={writer.path}", flush=True)
    cached_pending: dict[str, int] = {}
    last_rescan = 0.0
    last_purge = 0.0
    last_session = ""
    while True:
        session_id = _resolve_session_id(SEGMENT_ROOT)
        if not session_id:
            writer.mark_no_session()
            print("未找到采集会话，等待中…", flush=True)
            time.sleep(POLL_INTERVAL_S)
            continue
        if session_id != last_session:
            writer.reset_session(session_id)
            last_session = session_id
        if _foreground_upload_active():
            time.sleep(POLL_INTERVAL_S)
            continue
        now = time.monotonic()
        if session_id not in cached_pending or now - last_rescan >= 30.0:
            cached_pending[session_id] = len(
                list_closed_pending_segments(SEGMENT_ROOT, session_id)
            )
            last_rescan = now
        n = int(cached_pending.get(session_id, 0))
        n = _trim_over_cap(session_id, n)
        n = _trim_for_disk_quota(session_id, n)
        cached_pending[session_id] = n
        skipped = scan_skipped_segments(SEGMENT_ROOT, session_id)
        phase = "uploading" if n > 0 else "idle"
        writer.refresh_queue(
            session_id=session_id,
            pending=n,
            skipped_segments=skipped,
            phase=phase,
        )
        if PURGE_INTERVAL_S > 0 and now - last_purge >= PURGE_INTERVAL_S:
            purged = _purge_uploaded_segments(session_id)
            if purged:
                print(f"已清理本机已上传段目录：{purged} 个", flush=True)
            last_purge = now
        if n > 0:
            prog = writer._state.get("progress") or {}
            completed = int(prog.get("completed") or 0)
            total = int(prog.get("total") or (n + completed + len(skipped)))
            print(
                f"正在上传：已完成 {completed}/{total} 段，剩余 {n} 段",
                flush=True,
            )
            uploaded = _upload_once(session_id, limit=UPLOAD_BATCH)
            if uploaded > 0:
                cached_pending[session_id] = max(0, n - uploaded)
        else:
            print("全部待传段已上传完成（本机队列为空）", flush=True)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
