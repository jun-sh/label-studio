"""Edge segment store: capture writes closed segments locally; upload is decoupled."""

from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, TextIO

import numpy as np

from ego_capture_studio.capture.camera_intrinsics import (
    INTRINSICS_REL_PATH,
    write_camera_intrinsics_json,
)
from ego_capture_studio.capture.intrinsics_store import write_session_intrinsics
from ego_capture_studio.capture.ego_spec import OBS_STATE_DIM

SEGMENT_STORE_VERSION = 2
MANIFEST_SCHEMA_VERSION = 2

SegmentStatus = Literal[
    "RECORDING",
    "CLOSED",
    "UPLOADING",
    "UPLOADED",
    "UPLOAD_FAILED",
    "CORRUPT",
]

UPLOADABLE_STATUSES: frozenset[str] = frozenset({"CLOSED", "UPLOAD_FAILED"})
GC_ELIGIBLE_STATUS = "UPLOADED"

# Larger segments + batched persist (manufacturer FPS + Scheme A IO).
SEGMENT_MAX_FRAMES = int(os.environ.get("EGO_SEGMENT_MAX_FRAMES", "300"))
SEGMENT_MAX_SECONDS = float(os.environ.get("EGO_SEGMENT_MAX_SECONDS", "45"))
# Backpressure sleep threshold only — never triggers deletion of unuploaded segments.
SEGMENT_BACKPRESSURE_PENDING_MAX = int(
    os.environ.get("EGO_SEGMENT_BACKPRESSURE_PENDING_MAX")
    or os.environ.get("EGO_SEGMENT_MAX_PENDING", "24")
)
PENDING_FAST_RESCAN_S = max(5.0, float(os.environ.get("EGO_PENDING_FAST_RESCAN_S", "30")))
SEGMENT_ASYNC_DELETE = os.environ.get("EGO_SEGMENT_ASYNC_DELETE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
_pending_delete_lock = threading.Lock()
_pending_delete_threads: list[threading.Thread] = []
SEGMENT_BACKPRESSURE_SLEEP_S = float(os.environ.get("EGO_SEGMENT_BACKPRESSURE_SLEEP_S", "0.02"))
_bp_pending = (
    os.environ.get("SEGMENT_BACKPRESSURE_PENDING")
    or os.environ.get("EGO_SEGMENT_BACKPRESSURE_PENDING")
    or "1"
)
SEGMENT_BACKPRESSURE_PENDING = _bp_pending.strip().lower() in ("1", "true", "yes")
SEGMENT_PERSIST_QUEUE_MAX = int(os.environ.get("EGO_SEGMENT_PERSIST_QUEUE_MAX", "2048"))
SEGMENT_PERSIST_BATCH_FRAMES = int(os.environ.get("EGO_SEGMENT_PERSIST_BATCH_FRAMES", "64"))
SEGMENT_ROWS_BUFFER_LINES = int(os.environ.get("EGO_SEGMENT_ROWS_BUFFER_LINES", "64"))
SEGMENT_FSYNC_ON_CLOSE = os.environ.get("EGO_SEGMENT_FSYNC_ON_CLOSE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
SEGMENT_CHECKPOINT_EVERY = int(os.environ.get("EGO_SEGMENT_CHECKPOINT_EVERY", "300"))
SEGMENT_REGISTRY_ON_CLOSE_ONLY = os.environ.get("EGO_SEGMENT_REGISTRY_ON_CLOSE_ONLY", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
SEGMENT_PERSIST_BLOCK_S = float(os.environ.get("EGO_SEGMENT_PERSIST_BLOCK_S", "2.0"))
SEGMENT_FRAME_BIN = os.environ.get("SEGMENT_FRAME_BIN", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
SEGMENT_MCAP = os.environ.get("SEGMENT_MCAP", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
if SEGMENT_MCAP:
    pass
elif not SEGMENT_FRAME_BIN:
    raise RuntimeError("SEGMENT_FRAME_BIN=1 is required (JPEG DLB1 frame bins only)")
SEGMENT_PERSIST_WORKERS = max(1, int(os.environ.get("SEGMENT_PERSIST_WORKERS", "2")))
SEGMENT_FINALIZE_ASYNC = os.environ.get("SEGMENT_FINALIZE_ASYNC", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
SESSION_ROLL_FRAMES = int(os.environ.get("EGO_SESSION_ROLL_FRAMES", "0"))
# Hot open segment on tmpfs; fsync + move to EGO_SEGMENT_ROOT on close (Scheme A IO).
SEGMENT_ACTIVE_ROOT = Path(
    os.environ.get("EGO_SEGMENT_ACTIVE_ROOT", "/dev/shm/ego-capture-active")
)


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex}"


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_upload_meta() -> dict[str, Any]:
    return {
        "attempts": 0,
        "last_attempt_at": None,
        "last_error": None,
        "remote_ack_at": None,
    }


def _default_integrity_meta(*, ok: bool = True, issues: list[str] | None = None) -> dict[str, Any]:
    return {
        "checked_at": _utc_now_iso(),
        "ok": ok,
        "issues": list(issues or []),
    }


def _count_jsonl_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                count += 1
    return count


def manifest_status(manifest: dict[str, Any]) -> SegmentStatus:
    """Resolve segment status from v2 or legacy v1 manifest fields."""
    if int(manifest.get("manifest_schema_version") or 1) >= 2:
        raw = str(manifest.get("status") or "RECORDING").strip().upper()
        if raw in UPLOADABLE_STATUSES | {GC_ELIGIBLE_STATUS, "UPLOADING", "RECORDING", "CORRUPT"}:
            return raw  # type: ignore[return-value]
        return "RECORDING"
    if not bool(manifest.get("closed")):
        return "RECORDING"
    if bool(manifest.get("uploaded")):
        return "UPLOADED"
    return "CLOSED"


def read_manifest(segment_dir: Path) -> dict[str, Any]:
    manifest_path = segment_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing manifest: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _capture_meta_fields(manifest: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("sync_mode", "frame_interval_ms", "capture_fps", "imu_hz"):
        if key in manifest:
            out[key] = manifest[key]
    return out


def write_manifest_v2(segment_dir: Path, payload: dict[str, Any]) -> None:
    """Write manifest schema v2 (status enum only; no closed/uploaded bools)."""
    body = dict(payload)
    body["manifest_schema_version"] = MANIFEST_SCHEMA_VERSION
    body.pop("closed", None)
    body.pop("uploaded", None)
    body.pop("uploadedAt", None)
    manifest_path = segment_dir / "manifest.json"
    manifest_path.write_text(json.dumps(body, separators=(",", ":")) + "\n", encoding="utf-8")


def check_segment_integrity(
    segment_dir: Path,
    *,
    require_imu: bool | None = None,
) -> tuple[bool, list[str]]:
    """Appendix A integrity checks for a closed segment directory."""
    issues: list[str] = []
    manifest_path = segment_dir / "manifest.json"
    if not manifest_path.is_file():
        return False, ["missing_manifest"]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, ["manifest_unparseable"]

    frame_count = int(manifest.get("frame_count") or 0)
    if frame_count <= 0:
        issues.append("invalid_frame_count")

    storage_format = str(manifest.get("storage_format") or ("mcap" if SEGMENT_MCAP else "dlb1"))
    if storage_format == "mcap":
        mcap_path = segment_dir / "segment.mcap"
        if not mcap_path.is_file():
            issues.append("missing_segment_mcap")
        elif mcap_path.stat().st_size < 64:
            issues.append("empty_segment_mcap")
        else:
            try:
                from ego_capture_studio.capture.mcap_segment_writer import summarize_mcap_segment
            except ImportError:
                from mcap_segment_writer import summarize_mcap_segment

            # A segment left truncated by an abrupt stop makes the reader raise while
            # parsing its footer. This function runs on the capture process's startup
            # path (orphan reconciliation) and on every segment rotation, so an escaping
            # exception kills capture and the restart lands on the same unreadable file.
            # That loop took ego-001 down twice today. An unreadable segment is a corrupt
            # segment; report it and let rotation continue.
            try:
                summary = summarize_mcap_segment(mcap_path)
            except Exception as exc:
                issues.append(f"mcap_unreadable:{type(exc).__name__}")
                return False, issues

            cam_topics = [f"/ego/camera/{k}" for k in ("front_left", "front_right", "rear_left", "rear_right")]
            cam_counts = [int(summary.get("topics", {}).get(t, 0)) for t in cam_topics]
            if cam_counts and (min(cam_counts) <= 0 or len(set(cam_counts)) != 1):
                issues.append(
                    f"mcap_camera_parity_mismatch:min={min(cam_counts)},max={max(cam_counts)}",
                )
            try:
                from ego_capture_studio.capture.strict_fps_gate import check_mcap_strict_fps
            except ImportError:
                from strict_fps_gate import check_mcap_strict_fps

            fps_ok, fps_issues = check_mcap_strict_fps(mcap_path, frame_count)
            issues.extend(fps_issues)
        return len(issues) == 0, issues

    rows_path = segment_dir / "rows.jsonl"
    if not rows_path.is_file():
        issues.append("missing_rows_jsonl")
    else:
        row_lines = _count_jsonl_lines(rows_path)
        if row_lines != frame_count:
            issues.append(f"rows_count_mismatch:{row_lines}!={frame_count}")

    frames_dir = segment_dir / "frames"
    if not frames_dir.is_dir():
        issues.append("missing_frames_dir")
    else:
        bin_count = len(list(frames_dir.glob("*.bin")))
        if bin_count != frame_count:
            issues.append(f"frame_bin_count_mismatch:{bin_count}!={frame_count}")

    if require_imu is None:
        station = os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001"
        optional = os.environ.get("EGO_SEGMENT_IMU_OPTIONAL", "0").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        require_imu = station == "ego-001" and not optional

    imu_path = segment_dir / "imu_raw.jsonl"
    if require_imu:
        if not imu_path.is_file():
            issues.append("missing_imu_raw")
        elif _count_jsonl_lines(imu_path) < 1:
            issues.append("empty_imu_raw")

    return len(issues) == 0, issues


def can_gc_segment(segment_dir: Path) -> bool:
    try:
        manifest = read_manifest(segment_dir)
    except (OSError, json.JSONDecodeError, FileNotFoundError):
        return False
    return manifest_status(manifest) == GC_ELIGIBLE_STATUS


def list_uploaded_segments(root: Path, session_id: str) -> list[Path]:
    return _list_segments_by_status(
        root / "sessions" / session_id / "segments",
        session_id=session_id,
        statuses=frozenset({GC_ELIGIBLE_STATUS}),
    )


def scan_orphan_active_segments(active_root: Path, session_id: str) -> list[Path]:
    """Detect tmpfs active dirs left behind by crash (orphan_active)."""
    base = active_root / "sessions" / session_id / "segments"
    if not base.is_dir():
        return []
    orphans: list[Path] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        try:
            manifest = read_manifest(child)
        except (OSError, json.JSONDecodeError, FileNotFoundError):
            orphans.append(child)
            continue
        if manifest_status(manifest) == "RECORDING":
            orphans.append(child)
    return orphans


def finalize_segment_manifest_after_persist(segment_dir: Path) -> SegmentStatus:
    """Run integrity checks after rows/frames are flushed, then set CLOSED or CORRUPT."""
    manifest_path = segment_dir / "manifest.json"
    if not manifest_path.is_file():
        return reconcile_orphan_active_segment(segment_dir)
    ok, issues = check_segment_integrity(segment_dir)
    status: SegmentStatus = "CLOSED" if ok else "CORRUPT"
    if not manifest_path.is_file():
        return reconcile_orphan_active_segment(segment_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = {
        **{k: manifest[k] for k in manifest if k not in ("closed", "uploaded", "uploadedAt")},
        "status": status,
        "closed_at": _utc_now_iso(),
        "upload": manifest.get("upload") if isinstance(manifest.get("upload"), dict) else _default_upload_meta(),
        "integrity": _default_integrity_meta(ok=ok, issues=issues),
        **_capture_meta_fields(manifest),
    }
    write_manifest_v2(segment_dir, payload)
    return status


def reconcile_orphan_active_segment(segment_dir: Path) -> SegmentStatus:
    """Finalize a crash orphan on tmpfs: integrity check → CLOSED or CORRUPT."""
    ok, issues = check_segment_integrity(segment_dir)
    if "missing_manifest" in issues or "manifest_unparseable" in issues:
        issues.append("orphan_active")
        ok = False
    status: SegmentStatus = "CLOSED" if ok else "CORRUPT"
    if not ok and "orphan_active" not in issues:
        issues.append("orphan_active")
    try:
        manifest = read_manifest(segment_dir)
    except (OSError, json.JSONDecodeError, FileNotFoundError):
        manifest = {
            "segment_id": segment_dir.name,
            "session_id": "",
            "start_frame_index": 0,
            "end_frame_index": 0,
            "frame_count": 0,
            "created_at": _utc_now_iso(),
        }
    payload = {
        **{k: manifest[k] for k in manifest if k not in ("closed", "uploaded", "uploadedAt")},
        "segment_id": manifest.get("segment_id") or segment_dir.name,
        "session_id": manifest.get("session_id") or "",
        "status": status,
        "closed_at": _utc_now_iso(),
        "upload": manifest.get("upload") if isinstance(manifest.get("upload"), dict) else _default_upload_meta(),
        "integrity": _default_integrity_meta(ok=ok, issues=issues),
        **_capture_meta_fields(manifest),
    }
    write_manifest_v2(segment_dir, payload)
    return status


def build_lerobot_row(
    *,
    frame_index: int,
    timestamp_ns: int,
    imu6: np.ndarray,
    task: str,
    camera_ts_offset_ns: dict[str, int] | None = None,
) -> dict[str, Any]:
    # Raw capture rows: sensors + task only. Pose/hands are produced offline by ego-process convert.
    row: dict[str, Any] = {
        "frame_index": frame_index,
        "timestamp_ns": timestamp_ns,
        "task": task,
        "observation.state": imu6.reshape(OBS_STATE_DIM).astype(float).tolist(),
        "action": [0.0],
    }
    if camera_ts_offset_ns:
        # Nanoseconds relative to primary camera at this grid tick (not grid phase).
        row["camera_ts_offset_ns"] = {k: int(v) for k, v in camera_ts_offset_ns.items()}
    return row


def segment_file_key(frame_index: int, video_key: str) -> str:
    return f"{frame_index}__{video_key.replace('.', '_')}"


@dataclass(frozen=True)
class _PersistJob:
    session_id: str
    segment_id: str
    frame_index: int
    timestamp_ns: int
    camera_jpegs: dict[str, bytes]
    imu6: np.ndarray
    task: str
    camera_ts_offset_ns: dict[str, int] | None = None
    imu_raw_batch: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class _ImuRawPersistJob:
    session_id: str
    segment_id: str
    records: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _FinalizeJob:
    segment_id: str
    active_dir: Path


@dataclass(frozen=True)
class _SegmentCloseJob:
    session_id: str
    segment_id: str


PersistWorkItem = _PersistJob | _ImuRawPersistJob | _SegmentCloseJob


@dataclass
class SegmentManifest:
    segment_id: str
    session_id: str
    start_frame_index: int
    end_frame_index: int
    frame_count: int
    status: SegmentStatus
    created_at: str
    closed_at: str | None = None
    upload: dict[str, Any] = field(default_factory=_default_upload_meta)
    integrity: dict[str, Any] = field(default_factory=lambda: _default_integrity_meta())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _OpenSegmentWriter:
    """One rows.jsonl handle + line buffer per open segment (persist thread only)."""

    def __init__(self, segment_dir: Path) -> None:
        self.segment_dir = segment_dir
        self.frames_dir = segment_dir / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self._rows_path = segment_dir / "rows.jsonl"
        self._rows_fp: TextIO = open(self._rows_path, "a", encoding="utf-8", buffering=256 * 1024)
        self._row_lines: list[str] = []
        self._imu_raw_path = segment_dir / "imu_raw.jsonl"
        self._imu_raw_fp: TextIO | None = None
        self._imu_raw_lines: list[str] = []

    def write_frame(self, job: _PersistJob) -> None:
        prefix = f"{job.frame_index:08d}"
        from ego_capture_studio.capture.frame_bin_codec import pack_frame_bin

        out = self.frames_dir / f"{prefix}.bin"
        with open(out, "wb", buffering=1024 * 1024) as img_fp:
            img_fp.write(pack_frame_bin(job.camera_jpegs))
        row = build_lerobot_row(
            frame_index=job.frame_index,
            timestamp_ns=job.timestamp_ns,
            imu6=job.imu6,
            task=job.task,
            camera_ts_offset_ns=job.camera_ts_offset_ns,
        )
        self._row_lines.append(json.dumps(row, separators=(",", ":")) + "\n")
        if job.imu_raw_batch:
            self._append_imu_raw_records(job.imu_raw_batch)
        if len(self._row_lines) >= SEGMENT_ROWS_BUFFER_LINES:
            self._flush_rows()

    def _ensure_imu_raw_fp(self) -> TextIO:
        if self._imu_raw_fp is None:
            self._imu_raw_fp = open(self._imu_raw_path, "a", encoding="utf-8", buffering=256 * 1024)
        return self._imu_raw_fp

    def _append_imu_raw_records(self, records: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> None:
        if not records:
            return
        for rec in records:
            self._imu_raw_lines.append(json.dumps(rec, separators=(",", ":")) + "\n")
        if len(self._imu_raw_lines) >= SEGMENT_ROWS_BUFFER_LINES:
            self._flush_imu_raw()

    def _flush_imu_raw(self) -> None:
        if not self._imu_raw_lines:
            return
        fp = self._ensure_imu_raw_fp()
        fp.write("".join(self._imu_raw_lines))
        self._imu_raw_lines.clear()

    def append_imu_raw_records(self, records: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> None:
        self._append_imu_raw_records(records)
        self._flush_imu_raw()

    def _flush_rows(self) -> None:
        if not self._row_lines:
            return
        self._rows_fp.write("".join(self._row_lines))
        self._row_lines.clear()

    def close(self) -> None:
        self._flush_rows()
        self._flush_imu_raw()
        self._rows_fp.flush()
        if self._imu_raw_fp is not None:
            self._imu_raw_fp.flush()
            if SEGMENT_FSYNC_ON_CLOSE:
                try:
                    os.fsync(self._imu_raw_fp.fileno())
                except OSError:
                    pass
            self._imu_raw_fp.close()
            self._imu_raw_fp = None
        if SEGMENT_FSYNC_ON_CLOSE:
            try:
                os.fsync(self._rows_fp.fileno())
            except OSError:
                pass
        self._rows_fp.close()
        if SEGMENT_FSYNC_ON_CLOSE:
            try:
                fd = os.open(self.segment_dir, os.O_RDONLY)
                os.fsync(fd)
                os.close(fd)
            except OSError:
                pass


class _OpenSegmentMcapWriter:
    """MCAP persist adapter (SEGMENT_MCAP=1 only)."""

    def __init__(
        self,
        segment_dir: Path,
        *,
        session_id: str,
        segment_id: str,
        task: str,
    ) -> None:
        from ego_capture_studio.capture.mcap_segment_writer import McapSegmentWriter, mcap_video_codec_from_env

        station_id = os.environ.get("EGO_STATION_ID", "ego-mcap-pilot").strip() or "ego-mcap-pilot"
        self.segment_dir = segment_dir
        self._writer = McapSegmentWriter(
            segment_dir,
            session_id=session_id,
            segment_id=segment_id,
            station_id=station_id,
            task=task,
            video_codec=mcap_video_codec_from_env(),
        )
        self._writer.open()

    def write_frame(self, job: _PersistJob) -> None:
        row = build_lerobot_row(
            frame_index=job.frame_index,
            timestamp_ns=job.timestamp_ns,
            imu6=job.imu6,
            task=job.task,
            camera_ts_offset_ns=job.camera_ts_offset_ns,
        )
        self._writer.write_frame(
            frame_index=job.frame_index,
            timestamp_ns=job.timestamp_ns,
            camera_jpegs=job.camera_jpegs,
            row=row,
            camera_ts_offset_ns=(
                job.camera_ts_offset_ns.get("primary") if job.camera_ts_offset_ns else None
            ),
        )
        if job.imu_raw_batch:
            self._writer.append_imu_raw_records(job.imu_raw_batch)

    def append_imu_raw_records(self, records: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> None:
        self._writer.append_imu_raw_records(records)

    def close(self) -> None:
        self._writer.close()


def _new_open_segment_writer(
    segment_dir: Path,
    *,
    session_id: str,
    segment_id: str,
    task: str,
) -> _OpenSegmentWriter | _OpenSegmentMcapWriter:
    if SEGMENT_MCAP:
        return _OpenSegmentMcapWriter(
            segment_dir,
            session_id=session_id,
            segment_id=segment_id,
            task=task,
        )
    return _OpenSegmentWriter(segment_dir)


class SegmentCaptureWriter:
    """Append frames into rotating on-disk segments (batched async persist)."""

    def __init__(
        self,
        root: Path,
        *,
        session_id: str,
        task: str,
        quota_bytes: int,
        checkpoint_path: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.session_id = session_id
        self.task = task
        self.quota_bytes = max(1, int(quota_bytes))
        self.checkpoint_path = checkpoint_path
        self._lock = threading.Lock()
        self._next_frame_index = 0
        self._segment_seq = 0
        self._open_segment_id: str | None = None
        self._open_segment_dir: Path | None = None
        self._open_frame_count = 0
        self._open_started_mono = 0.0
        self._open_start_frame = 0
        self._persist_queue: queue.Queue[PersistWorkItem | None] = queue.Queue(
            maxsize=max(64, SEGMENT_PERSIST_QUEUE_MAX)
        )
        self._persist_stop = threading.Event()
        self._writer_lock = threading.Lock()
        self._open_writers: dict[str, _OpenSegmentWriter] = {}
        self._dropped_frames = 0
        self._persist_threads: list[threading.Thread] = []
        for i in range(SEGMENT_PERSIST_WORKERS):
            t = threading.Thread(
                target=self._persist_worker_loop,
                name=f"ego-segment-persist-{i}",
                daemon=True,
            )
            t.start()
            self._persist_threads.append(t)
        self._pending_count = -1
        self._pending_last_scan_mono = 0.0
        self._pre_segment_rotate_hooks: list[Callable[[], None]] = []
        self._closed_segment_ids: set[str] = set()
        self._finalize_queue: queue.Queue[_FinalizeJob | None] | None = None
        self._finalize_thread: threading.Thread | None = None
        if SEGMENT_FINALIZE_ASYNC:
            self._finalize_queue = queue.Queue(maxsize=8)
            self._finalize_thread = threading.Thread(
                target=self._finalize_worker_loop,
                name="ego-segment-finalize",
                daemon=True,
            )
            self._finalize_thread.start()
        self.root.mkdir(parents=True, exist_ok=True)
        self._session_dir().mkdir(parents=True, exist_ok=True)
        self._load_checkpoint()
        self._reconcile_orphan_active_on_init()
        self._pending_count = self._scan_pending_segment_count()

    def _reconcile_orphan_active_on_init(self) -> None:
        orphans = scan_orphan_active_segments(SEGMENT_ACTIVE_ROOT, self.session_id)
        if not orphans:
            return
        dest_parent = self._segments_dir()
        dest_parent.mkdir(parents=True, exist_ok=True)
        for active_dir in orphans:
            reconcile_orphan_active_segment(active_dir)
            dest_dir = dest_parent / active_dir.name
            if dest_dir.exists():
                shutil.rmtree(dest_dir, ignore_errors=True)
            if active_dir.is_dir():
                shutil.move(str(active_dir), str(dest_dir))

    @classmethod
    def from_env(
        cls,
        *,
        task: str,
        checkpoint_path: str | Path | None = None,
    ) -> SegmentCaptureWriter:
        station = os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001"
        root = Path(os.environ.get("EGO_SEGMENT_ROOT", f"/home/server/cache/{station}/segments"))
        gb = float(os.environ.get("EGO_SEGMENT_QUOTA_GB", "256"))
        session_id = os.environ.get("EGO_CAPTURE_SESSION_ID") or new_session_id()
        return cls(
            root,
            session_id=session_id,
            task=task,
            quota_bytes=int(gb * 1024**3),
            checkpoint_path=Path(checkpoint_path) if checkpoint_path else None,
        )

    def _session_dir(self) -> Path:
        return self.root / "sessions" / self.session_id

    def session_camera_intrinsics_path(self) -> Path:
        return self._session_dir() / INTRINSICS_REL_PATH

    def write_session_camera_intrinsics(self, document: dict[str, Any]) -> Path:
        """Persist EEPROM intrinsics once per session (uploaded via session_start)."""
        return write_session_intrinsics(self.root, self.session_id, document)

    def _segments_dir(self) -> Path:
        return self._session_dir() / "segments"

    def _active_segments_dir(self) -> Path:
        return SEGMENT_ACTIVE_ROOT / "sessions" / self.session_id / "segments"

    def _registry_path(self) -> Path:
        return self.root / "registry.json"

    def _load_registry(self) -> dict[str, Any]:
        p = self._registry_path()
        if not p.is_file():
            return {"version": SEGMENT_STORE_VERSION, "sessions": {}}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": SEGMENT_STORE_VERSION, "sessions": {}}
        raw.setdefault("sessions", {})
        return raw

    def _save_registry(self, reg: dict[str, Any]) -> None:
        tmp = self._registry_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, separators=(",", ":")) + "\n", encoding="utf-8")
        tmp.replace(self._registry_path())

    def _touch_registry_session(self, *, last_segment_id: str | None = None) -> None:
        if not SEGMENT_REGISTRY_ON_CLOSE_ONLY and last_segment_id is None:
            return
        reg = self._load_registry()
        ent = reg["sessions"].get(self.session_id) or {}
        ent["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if last_segment_id:
            ent["lastSegmentId"] = last_segment_id
            ent["pendingUpload"] = self.pending_segment_count(fast=True)
        reg["sessions"][self.session_id] = ent
        self._save_registry(reg)

    def _load_checkpoint(self) -> None:
        path = self.checkpoint_path or (self._session_dir() / "checkpoint.json")
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        sid = raw.get("sessionId")
        if isinstance(sid, str) and sid.strip():
            self.session_id = sid.strip()
        next_idx = raw.get("nextFrameIndex")
        if isinstance(next_idx, int) and next_idx >= 0:
            self._next_frame_index = next_idx
        seg_seq = raw.get("segmentSeq")
        if isinstance(seg_seq, int) and seg_seq >= 0:
            self._segment_seq = seg_seq

    def _persist_checkpoint(self) -> None:
        path = self.checkpoint_path or (self._session_dir() / "checkpoint.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SEGMENT_STORE_VERSION,
            "sessionId": self.session_id,
            "nextFrameIndex": self._next_frame_index,
            "segmentSeq": self._segment_seq,
            "task": self.task,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _scan_pending_segment_count(self) -> int:
        count = 0
        seg_dir = self._segments_dir()
        if not seg_dir.is_dir():
            return 0
        for child in seg_dir.iterdir():
            if not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest_status(m) in UPLOADABLE_STATUSES:
                count += 1
        return count

    def pending_segment_count(self, *, fast: bool = False) -> int:
        now = time.monotonic()
        if (
            fast
            and self._pending_count >= 0
            and (now - self._pending_last_scan_mono) < PENDING_FAST_RESCAN_S
        ):
            return self._pending_count
        scanned = self._scan_pending_segment_count()
        self._pending_count = scanned
        self._pending_last_scan_mono = now
        return scanned

    def _note_segment_finalized(self) -> None:
        if self._pending_count >= 0:
            self._pending_count += 1
        self._enforce_disk_quota()

    def _enforce_disk_quota(self) -> None:
        """Delete oldest UPLOADED segments only when local store exceeds quota."""
        while segment_store_bytes(self.root) > self.quota_bytes:
            uploaded = sorted(
                list_uploaded_segments(self.root, self.session_id),
                key=lambda p: p.name,
            )
            if not uploaded:
                break
            gc_segment_dir(uploaded[0])
            self._pending_count = -1

    def _enqueue_finalize(self, segment_id: str, active_dir: Path) -> None:
        if self._finalize_queue is not None:
            self._finalize_queue.put(_FinalizeJob(segment_id=segment_id, active_dir=active_dir))
            return
        self._finalize_closed_segment_dir(segment_id, active_dir)
        self._note_segment_finalized()

    def _finalize_worker_loop(self) -> None:
        assert self._finalize_queue is not None
        while True:
            job = self._finalize_queue.get()
            if job is None:
                self._finalize_queue.task_done()
                break
            try:
                self._finalize_closed_segment_dir(job.segment_id, job.active_dir)
                self._note_segment_finalized()
            finally:
                self._finalize_queue.task_done()

    def _wait_backpressure(self) -> None:
        if not SEGMENT_BACKPRESSURE_PENDING:
            return
        while self.pending_segment_count() >= SEGMENT_BACKPRESSURE_PENDING_MAX:
            time.sleep(SEGMENT_BACKPRESSURE_SLEEP_S)

    def _manifest_dict(self, manifest: SegmentManifest) -> dict[str, Any]:
        payload = manifest.to_dict()
        payload["sync_mode"] = "egoverse_30hz"
        payload["frame_interval_ms"] = int(os.environ.get("EGO_FRAME_INTERVAL_MS", "33"))
        payload["capture_fps"] = int(os.environ.get("EGO_CAPTURE_FPS", "30"))
        payload["imu_hz"] = int(os.environ.get("EGO_CAPTURE_IMU_HZ", "200"))
        if SEGMENT_MCAP:
            payload["storage_format"] = "mcap"
            payload["upload_protocol"] = "mcap"
        else:
            payload["storage_format"] = "dlb1"
        return payload

    def _write_manifest(self, segment_dir: Path, manifest: SegmentManifest) -> None:
        write_manifest_v2(segment_dir, self._manifest_dict(manifest))

    def _open_new_segment(self) -> str:
        self._segment_seq += 1
        segment_id = f"seg_{self._segment_seq:06d}"
        segment_dir = self._active_segments_dir() / segment_id
        segment_dir.mkdir(parents=True, exist_ok=True)
        self._open_segment_id = segment_id
        self._open_segment_dir = segment_dir
        self._open_frame_count = 0
        self._open_started_mono = time.monotonic()
        self._open_start_frame = self._next_frame_index
        manifest = SegmentManifest(
            segment_id=segment_id,
            session_id=self.session_id,
            start_frame_index=self._open_start_frame,
            end_frame_index=self._open_start_frame,
            frame_count=0,
            status="RECORDING",
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._write_manifest(segment_dir, manifest)
        if not SEGMENT_MCAP:
            (segment_dir / "rows.jsonl").touch()
        return segment_id

    def _ensure_open_segment(self) -> str:
        if self._open_segment_id is None or self._open_segment_dir is None:
            return self._open_new_segment()
        return self._open_segment_id

    def _finalize_closed_segment_dir(self, segment_id: str, active_dir: Path) -> None:
        """Move closed segment from tmpfs to archive root (atomic rename)."""
        dest_parent = self._segments_dir()
        dest_parent.mkdir(parents=True, exist_ok=True)
        dest_dir = dest_parent / segment_id
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        shutil.move(str(active_dir), str(dest_dir))

    def _close_open_segment_locked(self) -> str | None:
        if self._open_segment_id is None or self._open_segment_dir is None:
            return None
        segment_id = self._open_segment_id
        segment_dir = self._open_segment_dir
        end_idx = max(self._open_start_frame, self._next_frame_index - 1)
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            prev = json.loads((segment_dir / "manifest.json").read_text(encoding="utf-8"))
            created_at = prev.get("created_at") or created_at
        except (OSError, json.JSONDecodeError):
            pass
        manifest = SegmentManifest(
            segment_id=segment_id,
            session_id=self.session_id,
            start_frame_index=self._open_start_frame,
            end_frame_index=end_idx,
            frame_count=self._open_frame_count,
            status="RECORDING",
            created_at=created_at,
        )
        self._write_manifest(segment_dir, manifest)
        self._open_segment_id = None
        self._open_segment_dir = None
        self._open_frame_count = 0
        return segment_id

    def register_pre_segment_rotate_hook(self, hook: Callable[[], None]) -> None:
        """P1a: called on capture thread before closing a full segment (IDR flush)."""
        self._pre_segment_rotate_hooks.append(hook)

    def _invoke_pre_segment_rotate_hooks(self) -> None:
        for hook in self._pre_segment_rotate_hooks:
            try:
                hook()
            except Exception as exc:
                print(f"pre_segment_rotate_hook warning: {exc}", flush=True)

    def _rotate_segment_locked(self) -> None:
        self._invoke_pre_segment_rotate_hooks()
        closed_id = self._close_open_segment_locked()
        if closed_id:
            close_job = _SegmentCloseJob(session_id=self.session_id, segment_id=closed_id)
            self._offer_persist(close_job, blocking=True)
            self._touch_registry_session(last_segment_id=closed_id)
        self._open_new_segment()

    def _maybe_rotate_segment_locked(self) -> None:
        """Rotate before accepting a new frame when the current segment is full."""
        if self._open_segment_id is None:
            return
        elapsed = time.monotonic() - self._open_started_mono
        if self._open_frame_count <= 0:
            return
        if self._open_frame_count < SEGMENT_MAX_FRAMES and elapsed < SEGMENT_MAX_SECONDS:
            return
        self._rotate_segment_locked()

    def append_frame(
        self,
        *,
        timestamp_ns: int,
        camera_jpegs: dict[str, bytes],
        imu6: np.ndarray,
        camera_ts_offset_ns: dict[str, int] | None = None,
        imu_raw_batch: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    ) -> int:
        if not camera_jpegs:
            raise RuntimeError("append_frame requires camera_jpegs")
        self._wait_backpressure()
        job: _PersistJob
        with self._lock:
            self._ensure_open_segment()
            self._maybe_rotate_segment_locked()
            segment_id = self._open_segment_id
            assert segment_id is not None
            frame_index = self._next_frame_index
            job = _PersistJob(
                session_id=self.session_id,
                segment_id=segment_id,
                frame_index=frame_index,
                timestamp_ns=int(timestamp_ns),
                camera_jpegs=camera_jpegs,
                imu6=imu6,
                task=self.task,
                camera_ts_offset_ns=camera_ts_offset_ns,
                imu_raw_batch=tuple(imu_raw_batch or ()),
            )
            self._next_frame_index += 1
            self._open_frame_count += 1
            if SESSION_ROLL_FRAMES > 0 and self._next_frame_index % SESSION_ROLL_FRAMES == 0:
                self._rotate_segment_locked()
            if SEGMENT_CHECKPOINT_EVERY > 0 and self._next_frame_index % SEGMENT_CHECKPOINT_EVERY == 0:
                self._persist_checkpoint()
        self._offer_persist(job, blocking=False)
        return job.frame_index

    def append_imu_raw(self, records: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> None:
        if not records:
            return
        with self._lock:
            self._ensure_open_segment()
            segment_id = self._open_segment_id
            assert segment_id is not None
            job = _ImuRawPersistJob(
                session_id=self.session_id,
                segment_id=segment_id,
                records=tuple(records),
            )
        self._offer_persist(job, blocking=True)

    def _handle_segment_close(self, work: _SegmentCloseJob) -> None:
        """Close one segment on the persist thread; idempotent and race-safe."""
        segment_id = work.segment_id
        self._closed_segment_ids.add(segment_id)

        archive_dir = self._segments_dir() / segment_id
        if archive_dir.is_dir():
            try:
                existing = read_manifest(archive_dir)
            except (OSError, json.JSONDecodeError, FileNotFoundError):
                existing = None
            if existing is not None and manifest_status(existing) in (
                "CLOSED",
                "CORRUPT",
                "UPLOADED",
                GC_ELIGIBLE_STATUS,
            ):
                self._open_writers.pop(segment_id, None)
                return

        active_dir = self._active_segments_dir() / segment_id
        writer = self._open_writers.pop(segment_id, None)
        if writer is not None:
            writer.close()
        if not active_dir.is_dir():
            return
        try:
            finalize_segment_manifest_after_persist(active_dir)
            self._enqueue_finalize(segment_id, active_dir)
        except Exception as exc:
            print(
                f"segment_close_finalize_fail segment={segment_id} err={exc}",
                flush=True,
            )
            try:
                reconcile_orphan_active_segment(active_dir)
            except Exception as reconcile_exc:
                print(
                    f"segment_close_reconcile_fail segment={segment_id} err={reconcile_exc}",
                    flush=True,
                )
            self._enqueue_finalize(segment_id, active_dir)

    def _persist_frame_work(self, work: _PersistJob | _ImuRawPersistJob) -> None:
        if work.segment_id in self._closed_segment_ids:
            return
        seg_id = work.segment_id
        if seg_id not in self._open_writers:
            active_dir = self._active_segments_dir() / seg_id
            if not active_dir.is_dir():
                active_dir = self._segments_dir() / seg_id
            task = self.task
            if isinstance(work, _PersistJob):
                task = work.task
            self._open_writers[seg_id] = _new_open_segment_writer(
                active_dir,
                session_id=self.session_id,
                segment_id=seg_id,
                task=task,
            )
        if isinstance(work, _ImuRawPersistJob):
            self._open_writers[seg_id].append_imu_raw_records(work.records)
        else:
            self._open_writers[seg_id].write_frame(work)

    def _offer_persist(self, item: PersistWorkItem, *, blocking: bool) -> None:
        """Non-blocking for frames (drop oldest); segment close may block on flush."""
        if blocking:
            self._persist_queue.put(item)
            return
        while True:
            try:
                self._persist_queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    dropped = self._persist_queue.get_nowait()
                except queue.Empty:
                    return
                if isinstance(dropped, _SegmentCloseJob):
                    try:
                        self._persist_queue.put_nowait(dropped)
                    except queue.Full:
                        pass
                    continue
                if isinstance(dropped, _ImuRawPersistJob):
                    continue
                self._dropped_frames += 1

    def _persist_worker_loop(self) -> None:
        while True:
            batch: list[PersistWorkItem] = []
            item = self._persist_queue.get()
            if item is None:
                self._persist_queue.task_done()
                break
            batch.append(item)
            for _ in range(SEGMENT_PERSIST_BATCH_FRAMES - 1):
                try:
                    nxt = self._persist_queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    try:
                        self._persist_queue.put_nowait(None)
                    except queue.Full:
                        pass
                    break
                batch.append(nxt)
            frame_work = [w for w in batch if isinstance(w, (_PersistJob, _ImuRawPersistJob))]
            close_work = [w for w in batch if isinstance(w, _SegmentCloseJob)]
            for work in frame_work + close_work:
                with self._writer_lock:
                    if isinstance(work, _SegmentCloseJob):
                        self._handle_segment_close(work)
                    else:
                        self._persist_frame_work(work)
                self._persist_queue.task_done()

    def flush(self) -> None:
        with self._lock:
            closed_id = self._close_open_segment_locked()
            if closed_id:
                self._offer_persist(
                    _SegmentCloseJob(session_id=self.session_id, segment_id=closed_id),
                    blocking=True,
                )
                self._touch_registry_session(last_segment_id=closed_id)
        self._persist_queue.join()
        self._persist_checkpoint()

    def close(self) -> None:
        self.flush()
        self._persist_stop.set()
        for _ in self._persist_threads:
            try:
                self._persist_queue.put(None, timeout=5.0)
            except queue.Full:
                pass
        for t in self._persist_threads:
            if t.is_alive():
                t.join(timeout=10.0)
        with self._writer_lock:
            for seg_id, writer in list(self._open_writers.items()):
                writer.close()
                active_dir = self._active_segments_dir() / seg_id
                if active_dir.is_dir():
                    self._enqueue_finalize(seg_id, active_dir)
            self._open_writers.clear()
        if self._finalize_queue is not None:
            try:
                self._finalize_queue.put(None, timeout=5.0)
            except queue.Full:
                pass
            self._finalize_queue.join()
            if self._finalize_thread is not None and self._finalize_thread.is_alive():
                self._finalize_thread.join(timeout=120.0)

    @property
    def next_frame_index(self) -> int:
        return self._next_frame_index

    def persist_queue_depth(self) -> int:
        return self._persist_queue.qsize()

    def dropped_frame_count(self) -> int:
        return self._dropped_frames


def segment_store_bytes(root: Path) -> int:
    """Total bytes under segment root (all sessions)."""
    total = 0
    sessions = root / "sessions"
    if not sessions.is_dir():
        return 0
    for dirpath, _dirnames, filenames in os.walk(sessions, onerror=lambda _e: None):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return total


def _list_segments_by_status(
    seg_root: Path,
    *,
    session_id: str | None = None,
    statuses: frozenset[str],
) -> list[Path]:
    if not seg_root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(seg_root.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if session_id is not None:
            manifest_sid = str(m.get("session_id") or "").strip()
            if manifest_sid and manifest_sid != session_id:
                continue
        if manifest_status(m) in statuses:
            out.append(child)
    return out


def _list_closed_pending_under(
    seg_root: Path,
    *,
    session_id: str | None = None,
    include_uploaded: bool = False,
) -> list[Path]:
    statuses: set[str] = set(UPLOADABLE_STATUSES)
    if include_uploaded:
        statuses.add(GC_ELIGIBLE_STATUS)
    return _list_segments_by_status(seg_root, session_id=session_id, statuses=frozenset(statuses))


def list_closed_pending_segments(
    root: Path,
    session_id: str,
    *,
    include_uploaded: bool = False,
) -> list[Path]:
    out = _list_closed_pending_under(
        root / "sessions" / session_id / "segments",
        session_id=session_id,
        include_uploaded=include_uploaded,
    )
    legacy_root = root / "sessions" / "segments"
    if legacy_root.is_dir():
        for seg in _list_closed_pending_under(
            legacy_root,
            session_id=session_id,
            include_uploaded=include_uploaded,
        ):
            if seg not in out:
                out.append(seg)
    return out


def _delete_segment_dir(segment_dir: Path) -> None:
    if not can_gc_segment(segment_dir):
        raise RuntimeError(
            f"refusing to delete segment {segment_dir.name}: status is not {GC_ELIGIBLE_STATUS}"
        )
    shutil.rmtree(segment_dir, ignore_errors=True)


def gc_segment_dir(segment_dir: Path, *, sync: bool | None = None) -> None:
    """Delete a segment directory only when status == UPLOADED.

    Post-upload deletes must pass sync=True so the directory is gone before the
    upload process exits (async daemon threads are killed on interpreter exit).
    """
    if sync is None:
        sync = not SEGMENT_ASYNC_DELETE
    if sync:
        _delete_segment_dir(segment_dir)
        return

    def _run() -> None:
        try:
            _delete_segment_dir(segment_dir)
        finally:
            with _pending_delete_lock:
                _pending_delete_threads[:] = [t for t in _pending_delete_threads if t.is_alive()]

    thread = threading.Thread(
        target=_run,
        name=f"ego-seg-delete-{segment_dir.name}",
        daemon=True,
    )
    with _pending_delete_lock:
        _pending_delete_threads.append(thread)
    thread.start()


def flush_pending_segment_deletes(timeout_s: float = 600.0) -> int:
    """Wait for in-flight async segment deletes. Returns count still alive after timeout."""
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while True:
        with _pending_delete_lock:
            alive = [t for t in _pending_delete_threads if t.is_alive()]
            _pending_delete_threads[:] = alive
        if not alive:
            return 0
        if time.monotonic() >= deadline:
            return len(alive)
        for thread in alive:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=min(1.0, remaining))


def purge_uploaded_segments(root: Path, session_id: str, *, strict: bool = False) -> int:
    """Synchronously delete all UPLOADED segment dirs for one session."""
    removed = 0
    for segment_dir in list_uploaded_segments(root, session_id):
        _delete_segment_dir(segment_dir)
        removed += 1
    if strict:
        remaining = list_uploaded_segments(root, session_id)
        if remaining:
            names = ", ".join(p.name for p in remaining[:5])
            suffix = f" (+{len(remaining) - 5} more)" if len(remaining) > 5 else ""
            raise RuntimeError(
                f"purge_uploaded_segments: {len(remaining)} UPLOADED segment(s) remain "
                f"for {session_id}: {names}{suffix}"
            )
    return removed


def purge_all_uploaded_segments(root: Path, *, strict: bool = False) -> int:
    """Synchronously delete UPLOADED segment dirs across all sessions."""
    sessions = root / "sessions"
    if not sessions.is_dir():
        return 0
    removed = 0
    for sess in sorted(sessions.iterdir()):
        if not sess.is_dir() or not sess.name.startswith("sess_"):
            continue
        removed += purge_uploaded_segments(root, sess.name, strict=False)
    if strict:
        for sess in sorted(sessions.iterdir()):
            if not sess.is_dir() or not sess.name.startswith("sess_"):
                continue
            remaining = list_uploaded_segments(root, sess.name)
            if remaining:
                raise RuntimeError(
                    f"purge_all_uploaded_segments: {len(remaining)} UPLOADED segment(s) remain "
                    f"for {sess.name}"
                )
    return removed


def _update_manifest_status(
    segment_dir: Path,
    new_status: SegmentStatus,
    *,
    upload_patch: dict[str, Any] | None = None,
    integrity_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = read_manifest(segment_dir)
    upload = manifest.get("upload") if isinstance(manifest.get("upload"), dict) else _default_upload_meta()
    if upload_patch:
        upload.update(upload_patch)
    integrity = manifest.get("integrity") if isinstance(manifest.get("integrity"), dict) else _default_integrity_meta()
    if integrity_patch:
        integrity.update(integrity_patch)
    payload = {
        **{k: manifest[k] for k in manifest if k not in ("closed", "uploaded", "uploadedAt")},
        "status": new_status,
        "upload": upload,
        "integrity": integrity,
        **_capture_meta_fields(manifest),
    }
    write_manifest_v2(segment_dir, payload)
    return payload


def mark_segment_uploading(segment_dir: Path) -> None:
    manifest = read_manifest(segment_dir)
    status = manifest_status(manifest)
    allowed = set(UPLOADABLE_STATUSES) | {GC_ELIGIBLE_STATUS, "UPLOADING"}
    if status not in allowed:
        raise ValueError(f"cannot mark uploading from status {status}")
    upload = manifest.get("upload") if isinstance(manifest.get("upload"), dict) else _default_upload_meta()
    upload["attempts"] = int(upload.get("attempts") or 0) + 1
    upload["last_attempt_at"] = _utc_now_iso()
    upload["last_error"] = None
    _update_manifest_status(segment_dir, "UPLOADING", upload_patch=upload)


def mark_segment_upload_failed(segment_dir: Path, error: str) -> None:
    try:
        manifest = read_manifest(segment_dir)
    except FileNotFoundError:
        return
    status = manifest_status(manifest)
    if status not in {"UPLOADING", *UPLOADABLE_STATUSES}:
        raise ValueError(f"cannot mark upload failed from status {status}")
    upload = manifest.get("upload") if isinstance(manifest.get("upload"), dict) else _default_upload_meta()
    upload["last_error"] = str(error)[:500]
    upload["last_attempt_at"] = _utc_now_iso()
    _update_manifest_status(segment_dir, "UPLOAD_FAILED", upload_patch=upload)


def clear_segment_uploaded(segment_dir: Path) -> None:
    manifest = read_manifest(segment_dir)
    status = manifest_status(manifest)
    if status != GC_ELIGIBLE_STATUS:
        return
    upload = manifest.get("upload") if isinstance(manifest.get("upload"), dict) else _default_upload_meta()
    upload["remote_ack_at"] = None
    upload["last_error"] = None
    _update_manifest_status(segment_dir, "CLOSED", upload_patch=upload)


def mark_segment_uploaded(segment_dir: Path, *, delete: bool = False) -> None:
    manifest = read_manifest(segment_dir)
    status = manifest_status(manifest)
    if status == "CORRUPT":
        raise ValueError("cannot mark uploaded: segment is CORRUPT")
    if status == "RECORDING":
        raise ValueError("cannot mark uploaded: segment is still RECORDING")
    upload = manifest.get("upload") if isinstance(manifest.get("upload"), dict) else _default_upload_meta()
    upload["remote_ack_at"] = _utc_now_iso()
    upload["last_error"] = None
    _update_manifest_status(segment_dir, GC_ELIGIBLE_STATUS, upload_patch=upload)
    if delete:
        gc_segment_dir(segment_dir, sync=True)

