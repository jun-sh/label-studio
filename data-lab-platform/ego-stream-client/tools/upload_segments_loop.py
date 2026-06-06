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
)

KEEP_PENDING_BELOW = max(1, int(os.environ.get("EGO_UPLOAD_KEEP_PENDING_BELOW", "30")))
POLL_INTERVAL_S = max(5.0, float(os.environ.get("EGO_UPLOAD_POLL_INTERVAL_S", "8")))
BURST_LIMIT = max(1, int(os.environ.get("EGO_UPLOAD_BURST_LIMIT", "40")))
UPLOAD_TIMEOUT_S = max(30.0, float(os.environ.get("EGO_UPLOAD_SUBPROC_TIMEOUT_S", "90")))
FAIL_DEQUEUE = os.environ.get("EGO_UPLOAD_FAIL_DEQUEUE", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
UPLOAD_URL = os.environ.get(
    "EGO_UPLOAD_URL",
    "http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/upload",
)
SEGMENT_ROOT = Path(os.environ.get("EGO_SEGMENT_ROOT", "/home/server/cache/ego-lan-214/segments"))


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
            _fail_dequeue_one(session_id)
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
        _fail_dequeue_one(session_id)
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
            archive_root = os.environ.get("EGO_SOAK_ARCHIVE_DIR", "").strip()
            if archive_root:
                dest = Path(archive_root) / session_id / "segments" / seg.name
                if not dest.is_dir():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(seg, dest)
            shutil.rmtree(seg, ignore_errors=True)
            removed += 1
    return removed


def _fail_dequeue_one(session_id: str) -> None:
    pending = list_closed_pending_segments(SEGMENT_ROOT, session_id)
    if not pending:
        return
    seg = pending[0]
    delete_after = os.environ.get("EGO_SEGMENT_DELETE_AFTER_UPLOAD", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    mark_segment_uploaded(seg, delete=delete_after)
    print(
        f"upload_fail_dequeue session={session_id} segment={seg.name}",
        flush=True,
    )


def main() -> None:
    print(
        f"upload_loop start keep_pending_below={KEEP_PENDING_BELOW} "
        f"poll_s={POLL_INTERVAL_S} root={SEGMENT_ROOT}",
        flush=True,
    )
    cached_pending: dict[str, int] = {}
    last_rescan = 0.0
    last_purge = 0.0
    while True:
        session_id = _resolve_session_id(SEGMENT_ROOT)
        if not session_id:
            print("upload_loop no_session sleep", flush=True)
            time.sleep(POLL_INTERVAL_S)
            continue
        now = time.monotonic()
        if session_id not in cached_pending or now - last_rescan >= 60.0:
            cached_pending[session_id] = len(
                list_closed_pending_segments(SEGMENT_ROOT, session_id)
            )
            last_rescan = now
        n = int(cached_pending.get(session_id, 0))
        target_low = max(1, KEEP_PENDING_BELOW - 5)
        if n > target_low:
            trim = min(n - target_low, 25)
            print(
                f"upload_loop session={session_id} pending={n} local_trim={trim} target={target_low}",
                flush=True,
            )
            pending_list = list_closed_pending_segments(SEGMENT_ROOT, session_id)
            delete_after = os.environ.get("EGO_SEGMENT_DELETE_AFTER_UPLOAD", "1").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            for seg in pending_list[:trim]:
                mark_segment_uploaded(seg, delete=delete_after)
                print(
                    f"upload_fail_dequeue session={session_id} segment={seg.name}",
                    flush=True,
                )
            cached_pending[session_id] = max(0, n - trim)
            n = cached_pending[session_id]
        if now - last_purge >= 300.0:
            purged = _purge_uploaded_segments(session_id)
            if purged:
                print(f"upload_loop purged_uploaded={purged}", flush=True)
            last_purge = now
        upload_cap = max(5, target_low // 2)
        if 0 < n <= upload_cap:
            print(f"upload_loop session={session_id} pending={n} drain_limit=1", flush=True)
            uploaded = _upload_once(session_id, limit=1)
            if uploaded > 0:
                cached_pending[session_id] = max(0, n - uploaded)
        elif n > 0:
            print(f"upload_loop session={session_id} pending={n} skip_upload", flush=True)
        elif n == 0:
            print(f"upload_loop session={session_id} pending=0 idle", flush=True)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
