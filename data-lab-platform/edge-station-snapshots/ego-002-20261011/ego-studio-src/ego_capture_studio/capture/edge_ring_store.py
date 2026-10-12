"""32GB session ring buffer on the capture host (edge primary store).

Persists frame payloads locally before upload; evicts oldest sessions when over quota.
Does not change capture or ingest protocols — used only by stream_upload.py.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

try:
    import cv2
except ImportError as e:  # pragma: no cover
    raise RuntimeError("opencv-python-headless required for edge ring store") from e

try:
    from ego_capture_studio.capture.frame_jpeg_codec import encode_rgb_to_jpeg
except ImportError:
    from frame_jpeg_codec import encode_rgb_to_jpeg  # type: ignore[no-redef]


REGISTRY_VERSION = 1
RING_PERSIST_QUEUE_MAX = int(os.environ.get("EGO_RING_PERSIST_QUEUE_MAX", "400"))
RING_REGISTRY_FLUSH_EVERY = int(os.environ.get("EGO_RING_REGISTRY_FLUSH_EVERY", "30"))
RING_REGISTRY_FLUSH_INTERVAL_S = float(os.environ.get("EGO_RING_REGISTRY_FLUSH_INTERVAL_S", "2.0"))
RING_EVICT_CHECK_EVERY = int(os.environ.get("EGO_RING_EVICT_CHECK_EVERY", "60"))


@dataclass
class FrameRef:
    frame_index: int
    timestamp_ns: int
    task: str
    imu6: list[float]
    camera_keys: list[str]


@dataclass(frozen=True)
class _PersistJob:
    session_id: str
    frame_index: int
    timestamp_ns: int
    camera_jpegs: dict[str, bytes]
    imu6: np.ndarray
    task: str


class EdgeRingStore:
    def __init__(self, root: Path, *, quota_bytes: int) -> None:
        self.root = Path(root)
        self.quota_bytes = max(1, int(quota_bytes))
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "sessions").mkdir(parents=True, exist_ok=True)
        self._registry_cache: dict[str, Any] | None = None
        self._registry_dirty = False
        self._last_registry_flush = time.monotonic()
        self._frames_since_registry_flush = 0
        self._frames_since_evict_check = 0
        self._persist_queue: queue.Queue[_PersistJob | None] = queue.Queue(
            maxsize=max(32, RING_PERSIST_QUEUE_MAX)
        )
        self._persist_stop = threading.Event()
        self._persist_thread = threading.Thread(
            target=self._persist_worker_loop,
            name="ego-ring-persist",
            daemon=True,
        )
        self._persist_thread.start()

    @classmethod
    def from_env(cls) -> EdgeRingStore | None:
        if os.environ.get("EGO_EDGE_RING_ENABLED", "1").strip().lower() in ("0", "false", "no"):
            return None
        root = Path(os.environ.get("EGO_RING_ROOT", "/home/server/cache/ego-001/ring"))
        gb = float(os.environ.get("EGO_RING_QUOTA_GB", "32"))
        return cls(root, quota_bytes=int(gb * 1024**3))

    def _registry_path(self) -> Path:
        return self.root / "registry.json"

    def _load_registry(self) -> dict[str, Any]:
        p = self._registry_path()
        if not p.is_file():
            return {"version": REGISTRY_VERSION, "sessions": {}}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": REGISTRY_VERSION, "sessions": {}}
        if raw.get("version") != REGISTRY_VERSION:
            raw = {"version": REGISTRY_VERSION, "sessions": {}}
        raw.setdefault("sessions", {})
        return raw

    def _ensure_registry_cache(self) -> dict[str, Any]:
        if self._registry_cache is None:
            self._registry_cache = self._load_registry()
        return self._registry_cache

    def _save_registry(self, reg: dict[str, Any]) -> None:
        tmp = self._registry_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._registry_path())

    def _flush_registry(self, *, force: bool = False) -> None:
        with self._lock:
            if not self._registry_dirty and not force:
                return
            if self._registry_cache is None:
                return
            self._save_registry(self._registry_cache)
            self._registry_dirty = False
            self._last_registry_flush = time.monotonic()
            self._frames_since_registry_flush = 0

    def _maybe_flush_registry(self) -> None:
        if not self._registry_dirty:
            return
        if self._frames_since_registry_flush >= RING_REGISTRY_FLUSH_EVERY:
            self._flush_registry()
            return
        if (time.monotonic() - self._last_registry_flush) >= RING_REGISTRY_FLUSH_INTERVAL_S:
            self._flush_registry()

    def _session_dir(self, session_id: str) -> Path:
        return self.root / "sessions" / session_id

    def _frame_dir(self, session_id: str, frame_index: int) -> Path:
        return self._session_dir(session_id) / "frames" / f"{frame_index:08d}"

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        if not path.exists():
            return 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
        return total

    def total_bytes(self) -> int:
        return self._dir_size(self.root)

    def touch_session(self, session_id: str) -> None:
        with self._lock:
            reg = self._ensure_registry_cache()
            sessions = reg["sessions"]
            ent = sessions.get(session_id) or {}
            ent["startedAt"] = ent.get("startedAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ent["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            sessions[session_id] = ent
            self._registry_dirty = True
            self._session_dir(session_id).mkdir(parents=True, exist_ok=True)
        self._flush_registry(force=True)

    def persist_frame(
        self,
        session_id: str,
        *,
        frame_index: int,
        timestamp_ns: int,
        camera_frames: dict[str, np.ndarray],
        imu6: np.ndarray,
        task: str,
    ) -> None:
        """Legacy RGB path — encodes then queues async persist."""
        jpegs: dict[str, bytes] = {}
        for key, rgb in camera_frames.items():
            if rgb is None:
                continue
            jpegs[key] = encode_rgb_to_jpeg(rgb)
        self.persist_frame_jpegs(
            session_id,
            frame_index=frame_index,
            timestamp_ns=timestamp_ns,
            camera_jpegs=jpegs,
            imu6=imu6,
            task=task,
        )

    def persist_frame_jpegs(
        self,
        session_id: str,
        *,
        frame_index: int,
        timestamp_ns: int,
        camera_jpegs: dict[str, bytes],
        imu6: np.ndarray,
        task: str,
    ) -> None:
        """Queue frame for async disk write (non-blocking for capture thread)."""
        if not camera_jpegs:
            return
        job = _PersistJob(
            session_id=session_id,
            frame_index=int(frame_index),
            timestamp_ns=int(timestamp_ns),
            camera_jpegs=camera_jpegs,
            imu6=imu6,
            task=task,
        )
        try:
            self._persist_queue.put_nowait(job)
        except queue.Full:
            try:
                self._persist_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._persist_queue.put_nowait(job)
            except queue.Full:
                pass

    def _persist_worker_loop(self) -> None:
        while True:
            job = self._persist_queue.get()
            if job is None:
                self._persist_queue.task_done()
                self._flush_registry(force=True)
                break
            try:
                self._write_frame_job(job)
            except Exception:
                pass
            finally:
                self._persist_queue.task_done()

    def _update_registry_session(self, job: _PersistJob) -> None:
        with self._lock:
            reg = self._ensure_registry_cache()
            ent = reg["sessions"].get(job.session_id) or {}
            ent["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ent["lastFrameIndex"] = max(int(ent.get("lastFrameIndex", -1)), job.frame_index)
            reg["sessions"][job.session_id] = ent
            self._registry_dirty = True
        self._frames_since_registry_flush += 1
        self._maybe_flush_registry()

    def _write_frame_job(self, job: _PersistJob) -> None:
        frame_dir = self._frame_dir(job.session_id, job.frame_index)
        if (frame_dir / ".synced").is_file():
            return
        frame_dir.mkdir(parents=True, exist_ok=True)
        keys: list[str] = []
        for key, jpeg in job.camera_jpegs.items():
            safe = key.replace(".", "_") + ".jpg"
            (frame_dir / safe).write_bytes(jpeg)
            keys.append(key)
        meta = FrameRef(
            frame_index=job.frame_index,
            timestamp_ns=job.timestamp_ns,
            task=job.task,
            imu6=[float(x) for x in job.imu6.reshape(-1).tolist()],
            camera_keys=keys,
        )
        (frame_dir / "meta.json").write_text(
            json.dumps(asdict(meta), separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        self._update_registry_session(job)
        self._frames_since_evict_check += 1
        if self._frames_since_evict_check >= RING_EVICT_CHECK_EVERY:
            self._frames_since_evict_check = 0
            self.evict_if_needed(active_session_id=job.session_id)

    def mark_synced(self, session_id: str, frame_index: int) -> None:
        frame_dir = self._frame_dir(session_id, frame_index)
        if not frame_dir.is_dir():
            return
        try:
            (frame_dir / ".synced").write_text("1\n", encoding="utf-8")
            shutil.rmtree(frame_dir, ignore_errors=True)
        except OSError:
            pass

    def list_pending_indices(self, session_id: str, *, from_index: int = 0) -> list[int]:
        sess = self._session_dir(session_id) / "frames"
        if not sess.is_dir():
            return []
        out: list[int] = []
        for child in sess.iterdir():
            if not child.is_dir():
                continue
            try:
                idx = int(child.name)
            except ValueError:
                continue
            if idx < from_index:
                continue
            if (child / "meta.json").is_file():
                out.append(idx)
        out.sort()
        return out

    def load_frame_jpegs(
        self,
        session_id: str,
        frame_index: int,
    ) -> tuple[dict[str, bytes], int, np.ndarray, str] | None:
        """Load JPEG bytes from ring for backfill (no decode)."""
        frame_dir = self._frame_dir(session_id, frame_index)
        meta_path = frame_dir / "meta.json"
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        camera_jpegs: dict[str, bytes] = {}
        for key in meta.get("camera_keys") or []:
            safe = key.replace(".", "_") + ".jpg"
            img_path = frame_dir / safe
            if not img_path.is_file():
                continue
            camera_jpegs[key] = img_path.read_bytes()
        if not camera_jpegs:
            return None
        imu6 = np.array(meta.get("imu6") or [0.0] * 6, dtype=float)
        return (
            camera_jpegs,
            int(meta.get("timestamp_ns") or 0),
            imu6,
            str(meta.get("task") or ""),
        )

    def load_frame_job(
        self,
        session_id: str,
        frame_index: int,
    ) -> tuple[dict[str, np.ndarray], int, np.ndarray, str] | None:
        """Rebuild RGB arrays (decode) — prefer load_frame_jpegs for backfill."""
        loaded = self.load_frame_jpegs(session_id, frame_index)
        if loaded is None:
            return None
        camera_jpegs, timestamp_ns, imu6, task = loaded
        camera_frames: dict[str, np.ndarray] = {}
        for key, jpeg in camera_jpegs.items():
            bgr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if bgr is None:
                continue
            camera_frames[key] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return camera_frames, timestamp_ns, imu6, task

    def evict_if_needed(self, *, active_session_id: str | None) -> int:
        """Drop oldest sessions (by updatedAt) until under quota. Never evicts active session."""
        removed = 0
        while self.total_bytes() > self.quota_bytes:
            with self._lock:
                reg = self._ensure_registry_cache()
                sessions = reg.get("sessions") or {}
                candidates = [
                    (sid, ent.get("updatedAt") or ent.get("startedAt") or "")
                    for sid, ent in sessions.items()
                    if sid != active_session_id
                ]
                candidates.sort(key=lambda x: x[1])
                if not candidates:
                    break
                victim = candidates[0][0]
                sessions.pop(victim, None)
                self._registry_dirty = True
            self._flush_registry(force=True)
            victim_dir = self._session_dir(victim)
            if victim_dir.is_dir():
                shutil.rmtree(victim_dir, ignore_errors=True)
            removed += 1
        return removed

    def close(self) -> None:
        self._persist_stop.set()
        try:
            self._persist_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._persist_thread.is_alive():
            self._persist_thread.join(timeout=3.0)
