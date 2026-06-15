"""Edge segment store: capture writes closed segments locally; upload is decoupled."""

from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from ego_capture_studio.capture.camera_intrinsics import (
    INTRINSICS_REL_PATH,
    write_camera_intrinsics_json,
)
from ego_capture_studio.capture.ego_spec import OBS_HANDS_DIM, OBS_POSE_DIM, OBS_STATE_DIM
from ego_capture_studio.capture.lerobot_episode import identity_pose_xyzw

SEGMENT_STORE_VERSION = 1
# Larger segments + batched persist (manufacturer FPS + Scheme A IO).
SEGMENT_MAX_FRAMES = int(os.environ.get("EGO_SEGMENT_MAX_FRAMES", "300"))
SEGMENT_MAX_SECONDS = float(os.environ.get("EGO_SEGMENT_MAX_SECONDS", "45"))
SEGMENT_MAX_PENDING = int(os.environ.get("EGO_SEGMENT_MAX_PENDING", "10"))
PENDING_FAST_RESCAN_S = max(5.0, float(os.environ.get("EGO_PENDING_FAST_RESCAN_S", "30")))
SEGMENT_ASYNC_DELETE = os.environ.get("EGO_SEGMENT_ASYNC_DELETE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
SEGMENT_AUTO_PURGE_PENDING = os.environ.get("SEGMENT_AUTO_PURGE_PENDING", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
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
SEGMENT_PERSIST_WORKERS = max(1, int(os.environ.get("SEGMENT_PERSIST_WORKERS", "2")))
# Phase-2 POC: append H.264 elementary streams per camera (local only; upload still JPEG).
SEGMENT_H264 = os.environ.get("SEGMENT_H264", "0").strip().lower() in ("1", "true", "yes")
SEGMENT_H264_LEGACY_APPEND = os.environ.get("SEGMENT_H264_LEGACY_APPEND", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
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


def build_lerobot_row(
    *,
    frame_index: int,
    timestamp_ns: int,
    imu6: np.ndarray,
    task: str,
    camera_ts_offset_ns: dict[str, int] | None = None,
) -> dict[str, Any]:
    pose = identity_pose_xyzw()
    row: dict[str, Any] = {
        "frame_index": frame_index,
        "timestamp_ns": timestamp_ns,
        "task": task,
        "observation.state": imu6.reshape(OBS_STATE_DIM).astype(float).tolist(),
        "observation.pose": pose.astype(float).tolist(),
        "observation.hands": [0.0] * OBS_HANDS_DIM,
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


@dataclass(frozen=True)
class _FinalizeJob:
    segment_id: str
    active_dir: Path


@dataclass(frozen=True)
class _SegmentCloseJob:
    session_id: str
    segment_id: str


PersistWorkItem = _PersistJob | _SegmentCloseJob


@dataclass
class SegmentManifest:
    segment_id: str
    session_id: str
    start_frame_index: int
    end_frame_index: int
    frame_count: int
    closed: bool
    uploaded: bool
    created_at: str
    closed_at: str | None = None

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
        self._h264_buffers: dict[str, list[bytes]] = {}

    def write_frame(self, job: _PersistJob) -> None:
        prefix = f"{job.frame_index:08d}"
        if SEGMENT_H264 and not SEGMENT_H264_LEGACY_APPEND:
            for key, chunk in job.camera_jpegs.items():
                self._h264_buffers.setdefault(key, []).append(chunk)
        elif SEGMENT_H264 and SEGMENT_H264_LEGACY_APPEND:
            streams_dir = self.segment_dir / "streams"
            streams_dir.mkdir(parents=True, exist_ok=True)
            for key, chunk in job.camera_jpegs.items():
                safe = key.replace(".", "_") + ".h264"
                with open(streams_dir / safe, "ab", buffering=1024 * 1024) as fp:
                    fp.write(chunk)
        elif SEGMENT_FRAME_BIN:
            from ego_capture_studio.capture.frame_bin_codec import pack_frame_bin

            out = self.frames_dir / f"{prefix}.bin"
            with open(out, "wb", buffering=1024 * 1024) as img_fp:
                img_fp.write(pack_frame_bin(job.camera_jpegs))
        else:
            for key, jpeg in job.camera_jpegs.items():
                safe = key.replace(".", "_") + ".jpg"
                out = self.frames_dir / f"{prefix}__{safe}"
                with open(out, "wb", buffering=512 * 1024) as img_fp:
                    img_fp.write(jpeg)
        row = build_lerobot_row(
            frame_index=job.frame_index,
            timestamp_ns=job.timestamp_ns,
            imu6=job.imu6,
            task=job.task,
            camera_ts_offset_ns=job.camera_ts_offset_ns,
        )
        self._row_lines.append(json.dumps(row, separators=(",", ":")) + "\n")
        if len(self._row_lines) >= SEGMENT_ROWS_BUFFER_LINES:
            self._flush_rows()

    def _flush_rows(self) -> None:
        if not self._row_lines:
            return
        self._rows_fp.write("".join(self._row_lines))
        self._row_lines.clear()

    def close(self) -> None:
        if SEGMENT_H264 and not SEGMENT_H264_LEGACY_APPEND and self._h264_buffers:
            from ego_capture_studio.capture.segment_h264_mux import mux_h264_buffers_to_mp4

            mux_h264_buffers_to_mp4(self.segment_dir, self._h264_buffers)
            self._h264_buffers.clear()
        self._flush_rows()
        self._rows_fp.flush()
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
        self._pending_count = self._scan_pending_segment_count()

    @classmethod
    def from_env(
        cls,
        *,
        task: str,
        checkpoint_path: str | Path | None = None,
    ) -> SegmentCaptureWriter:
        root = Path(os.environ.get("EGO_SEGMENT_ROOT", "/home/server/cache/ego-lan-214/segments"))
        gb = float(os.environ.get("EGO_SEGMENT_QUOTA_GB", "4"))
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
        path = self.session_camera_intrinsics_path()
        write_camera_intrinsics_json(path, document)
        return path

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
            if m.get("closed") and not m.get("uploaded"):
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
        """Drop oldest pending segments when local store exceeds quota (queue, not archive)."""
        while segment_store_bytes(self.root) > self.quota_bytes:
            pending = sorted(
                list_closed_pending_segments(self.root, self.session_id),
                key=lambda p: p.name,
            )
            if len(pending) <= 1:
                break
            mark_segment_uploaded(pending[0], delete=True)
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

    def purge_oldest_pending_segments(self, *, keep: int | None = None) -> int:
        """Delete oldest closed-unuploaded segments (local only) to cap pending backlog."""
        keep_n = SEGMENT_MAX_PENDING - 1 if keep is None else int(keep)
        pending = sorted(
            list_closed_pending_segments(self.root, self.session_id),
            key=lambda p: p.name,
        )
        excess = max(0, len(pending) - keep_n)
        for seg in pending[:excess]:
            mark_segment_uploaded(seg, delete=True)
        if excess:
            self._pending_count = -1
        return excess

    def _enforce_pending_cap(self) -> None:
        """Drop oldest closed segments when backlog exceeds SEGMENT_MAX_PENDING."""
        if not SEGMENT_AUTO_PURGE_PENDING:
            return
        while self.pending_segment_count() >= SEGMENT_MAX_PENDING:
            if self.purge_oldest_pending_segments() <= 0:
                break

    def _wait_backpressure(self) -> None:
        self._enforce_pending_cap()
        if not SEGMENT_BACKPRESSURE_PENDING:
            return
        while self.pending_segment_count() >= SEGMENT_MAX_PENDING:
            if SEGMENT_AUTO_PURGE_PENDING and self.purge_oldest_pending_segments() > 0:
                continue
            time.sleep(SEGMENT_BACKPRESSURE_SLEEP_S)

    def _manifest_dict(self, manifest: SegmentManifest) -> dict[str, Any]:
        payload = manifest.to_dict()
        if os.environ.get("EGO_STRICT_20HZ", "0").strip().lower() in ("1", "true", "yes"):
            payload["sync_mode"] = "strict_20hz"
            payload["frame_interval_ms"] = int(os.environ.get("EGO_FRAME_INTERVAL_MS", "50"))
        return payload

    def _write_manifest(self, segment_dir: Path, manifest: SegmentManifest) -> None:
        (segment_dir / "manifest.json").write_text(
            json.dumps(self._manifest_dict(manifest), separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

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
            closed=False,
            uploaded=False,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._write_manifest(segment_dir, manifest)
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
            closed=True,
            uploaded=False,
            created_at=created_at,
            closed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._write_manifest(segment_dir, manifest)
        self._open_segment_id = None
        self._open_segment_dir = None
        self._open_frame_count = 0
        return segment_id

    def _rotate_segment_locked(self) -> None:
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
            )
            self._next_frame_index += 1
            self._open_frame_count += 1
            if SESSION_ROLL_FRAMES > 0 and self._next_frame_index % SESSION_ROLL_FRAMES == 0:
                self._rotate_segment_locked()
            if SEGMENT_CHECKPOINT_EVERY > 0 and self._next_frame_index % SEGMENT_CHECKPOINT_EVERY == 0:
                self._persist_checkpoint()
        self._offer_persist(job, blocking=False)
        return job.frame_index

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
            for work in batch:
                with self._writer_lock:
                    if isinstance(work, _SegmentCloseJob):
                        writer = self._open_writers.pop(work.segment_id, None)
                        active_dir = self._active_segments_dir() / work.segment_id
                        if writer is not None:
                            writer.close()
                        if active_dir.is_dir():
                            self._enqueue_finalize(work.segment_id, active_dir)
                    else:
                        seg_id = work.segment_id
                        if seg_id not in self._open_writers:
                            active_dir = self._active_segments_dir() / seg_id
                            if not active_dir.is_dir():
                                active_dir = self._segments_dir() / seg_id
                            self._open_writers[seg_id] = _OpenSegmentWriter(active_dir)
                        self._open_writers[seg_id].write_frame(work)
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


def _list_closed_pending_under(seg_root: Path, *, session_id: str | None = None) -> list[Path]:
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
        if m.get("closed") and not m.get("uploaded"):
            out.append(child)
    return out


def list_closed_pending_segments(root: Path, session_id: str) -> list[Path]:
    out = _list_closed_pending_under(root / "sessions" / session_id / "segments", session_id=session_id)
    legacy_root = root / "sessions" / "segments"
    if legacy_root.is_dir():
        for seg in _list_closed_pending_under(legacy_root, session_id=session_id):
            if seg not in out:
                out.append(seg)
    return out


def _delete_segment_dir(segment_dir: Path) -> None:
    shutil.rmtree(segment_dir, ignore_errors=True)


def mark_segment_uploaded(segment_dir: Path, *, delete: bool = False) -> None:
    manifest_path = segment_dir / "manifest.json"
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    m["uploaded"] = True
    m["uploadedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_path.write_text(json.dumps(m, separators=(",", ":")) + "\n", encoding="utf-8")
    if delete:
        if SEGMENT_ASYNC_DELETE:
            threading.Thread(
                target=_delete_segment_dir,
                args=(segment_dir,),
                name=f"ego-seg-delete-{segment_dir.name}",
                daemon=True,
            ).start()
        else:
            _delete_segment_dir(segment_dir)
