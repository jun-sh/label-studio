"""Frame-at-a-time upload client for Data Lab collection stations (meta+data+videos only)."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HEARTBEAT_INTERVAL_S = 15.0
DEFAULT_UPLOAD_QUEUE_MAXSIZE = 300
CHECKPOINT_VERSION = 1
UPLOAD_RETRY_BASE_S = 0.5
UPLOAD_RETRY_MAX_S = 5.0
STATION_TOKEN_HEADER = "X-Station-Token"

import numpy as np

try:
    import cv2
except ImportError as e:  # pragma: no cover
    raise RuntimeError("opencv-python-headless is required for stream upload") from e

from ego_capture_studio.capture.camera_map import ALL_LEROBOT_VIDEO_KEYS
from ego_capture_studio.capture.ego_spec import OBS_HANDS_DIM, OBS_POSE_DIM, OBS_STATE_DIM
from ego_capture_studio.capture.lerobot_episode import identity_pose_xyzw


@dataclass(frozen=True)
class _FrameJob:
    frame_index: int
    timestamp_ns: int
    camera_frames: dict[str, np.ndarray]
    imu6: np.ndarray
    task: str


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex}"


_logger = logging.getLogger("datalab.stream")
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)


def _log_stream(
    event: str,
    *,
    session_id: str | None = None,
    frame_index: int | None = None,
    **fields: object,
) -> None:
    parts = [
        "[datalab-stream]",
        f"session={session_id or '-'}",
        f"frame={'-' if frame_index is None else frame_index}",
        f"event={event}",
    ]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    _logger.info(" ".join(str(p) for p in parts))


class FrameStreamUploader:
    def __init__(
        self,
        upload_url: str,
        *,
        timeout_s: float = 30.0,
        heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
        upload_queue_maxsize: int = DEFAULT_UPLOAD_QUEUE_MAXSIZE,
        checkpoint_path: str | Path | None = None,
        station_token: str | None = None,
    ) -> None:
        self.upload_url = upload_url.rstrip("/")
        self.station_token = station_token or os.environ.get("STATION_UPLOAD_TOKEN") or None
        if not self.upload_url.endswith("/upload"):
            raise ValueError("upload_url must end with /upload")
        self.timeout_s = timeout_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.session_id: str | None = None
        self.next_frame_index = 0
        self._resume_task: str | None = None
        self._heartbeat_host: str | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._upload_queue: queue.Queue[_FrameJob | None] = queue.Queue(
            maxsize=max(1, int(upload_queue_maxsize))
        )
        self._upload_thread: threading.Thread | None = None
        self._stats_lock = threading.Lock()
        self._checkpoint_lock = threading.Lock()
        self._uploaded_frames = 0
        self._duplicate_frames = 0
        self._dropped_frames = 0
        self._last_server_total: int | None = None
        self._last_upload_error: str | None = None
        self._network_ok = True
        self._retry_backoff_s = UPLOAD_RETRY_BASE_S
        self._held_job: _FrameJob | None = None
        self._load_checkpoint()
        if self.session_id:
            _log_stream(
                "checkpoint_loaded",
                session_id=self.session_id,
                next_frame_index=self.next_frame_index,
            )

    def _log(
        self,
        event: str,
        *,
        frame_index: int | None = None,
        **fields: object,
    ) -> None:
        _log_stream(event, session_id=self.session_id, frame_index=frame_index, **fields)

    @staticmethod
    def _should_retry_upload(exc: BaseException) -> bool:
        if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
            return True
        if isinstance(exc, RuntimeError):
            msg = str(exc)
            if "upload failed HTTP" in msg:
                return any(code in msg for code in (" 500", " 502", " 503", " 504", " 429"))
            return True
        return True

    def _note_upload_success(self) -> None:
        with self._stats_lock:
            self._network_ok = True
            self._last_upload_error = None
        self._retry_backoff_s = UPLOAD_RETRY_BASE_S

    def _note_upload_failure(self, exc: BaseException) -> None:
        with self._stats_lock:
            was_ok = self._network_ok
            self._network_ok = False
            self._last_upload_error = str(exc)
        if was_ok:
            self._log("network_down", err=str(exc)[:160])

    def _retry_sleep(self) -> None:
        delay = self._retry_backoff_s
        self._retry_backoff_s = min(self._retry_backoff_s * 2.0, UPLOAD_RETRY_MAX_S)
        time.sleep(delay)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def _request(self, *, data: bytes, headers: dict[str, str]) -> dict[str, Any]:
        out_headers = dict(headers)
        if self.station_token:
            out_headers[STATION_TOKEN_HEADER] = self.station_token
        req = urllib.request.Request(
            self.upload_url,
            data=data,
            headers=out_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"upload failed HTTP {e.code}: {detail}") from e

    @staticmethod
    def _encode_multipart(
        *,
        fields: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
        boundary: str,
    ) -> bytes:
        crlf = b"\r\n"
        boundary_b = boundary.encode("ascii")
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.append(b"--" + boundary_b + crlf)
            chunks.append(
                f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf
            )
            chunks.append(value.encode("utf-8"))
            chunks.append(crlf)
        for name, (filename, file_data, content_type) in files.items():
            chunks.append(b"--" + boundary_b + crlf)
            chunks.append(
                (
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'
                ).encode()
                + crlf
            )
            chunks.append(f"Content-Type: {content_type}".encode() + crlf + crlf)
            chunks.append(file_data)
            chunks.append(crlf)
        chunks.append(b"--" + boundary_b + b"--" + crlf)
        return b"".join(chunks)

    def heartbeat(self, *, host: str | None = None) -> None:
        if host is not None:
            self._heartbeat_host = host
        self._post({"action": "heartbeat", "host": self._heartbeat_host})
        self._note_upload_success()
        self._log("heartbeat_ok", host=self._heartbeat_host or "-")
        self._start_periodic_heartbeat()

    def _start_periodic_heartbeat(self) -> None:
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._periodic_heartbeat_loop,
            name="datalab-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _periodic_heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self.heartbeat_interval_s):
            try:
                self._post({"action": "heartbeat", "host": self._heartbeat_host})
                if not self._network_ok:
                    self._note_upload_success()
                    self._log("network_up")
                self._log("heartbeat_ok", host=self._heartbeat_host or "-")
            except Exception as exc:
                self._note_upload_failure(exc)
                self._log("heartbeat_fail", err=str(exc)[:160])

    def stop_periodic_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._heartbeat_thread = None

    def _load_checkpoint(self) -> None:
        if self.checkpoint_path is None or not self.checkpoint_path.is_file():
            return
        try:
            raw = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if raw.get("version") != CHECKPOINT_VERSION:
            return
        session_id = raw.get("sessionId")
        next_idx = raw.get("nextFrameIndex")
        if isinstance(session_id, str) and session_id:
            self.session_id = session_id
        if isinstance(next_idx, int) and next_idx >= 0:
            self.next_frame_index = next_idx
        task = raw.get("task")
        if isinstance(task, str) and task:
            self._resume_task = task

    def _persist_checkpoint(self, *, next_frame_index: int, task: str | None = None) -> None:
        if self.checkpoint_path is None or not self.session_id:
            return
        payload = {
            "version": CHECKPOINT_VERSION,
            "sessionId": self.session_id,
            "nextFrameIndex": int(next_frame_index),
            "task": task,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with self._checkpoint_lock:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.checkpoint_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.checkpoint_path)

    def resume_task(self) -> str | None:
        return self._resume_task

    def start_session(
        self,
        *,
        task: str,
        video_shapes: dict[str, tuple[int, int]],
        session_id: str | None = None,
    ) -> str:
        self.session_id = session_id or self.session_id or new_session_id()
        shapes_out = {k: [int(h), int(w)] for k, (h, w) in video_shapes.items()}
        out = self._post(
            {
                "action": "session_start",
                "sessionId": self.session_id,
                "task": task,
                "videoShapes": shapes_out,
            }
        )
        self.session_id = out.get("sessionId") or self.session_id
        server_total = out.get("totalFrames")
        if isinstance(server_total, int) and server_total > self.next_frame_index:
            self.next_frame_index = server_total
        self._persist_checkpoint(next_frame_index=self.next_frame_index, task=task)
        self._log(
            "session_start",
            next_frame_index=self.next_frame_index,
            resumed=bool(out.get("resumed")),
        )
        return self.session_id

    @staticmethod
    def _encode_rgb_jpeg(rgb: np.ndarray) -> bytes:
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise RuntimeError("jpeg encode failed")
        return buf.tobytes()

    @staticmethod
    def _snapshot_frame_job(
        *,
        frame_index: int,
        timestamp_ns: int,
        camera_frames: dict[str, np.ndarray],
        imu6: np.ndarray,
        task: str,
    ) -> _FrameJob:
        # iter_synced_frames yields fresh RGB arrays each step; upload worker
        # finishes before the next yield overwrites OAK queues.
        frames_ref = {k: v for k, v in camera_frames.items() if v is not None}
        return _FrameJob(
            frame_index=int(frame_index),
            timestamp_ns=int(timestamp_ns),
            camera_frames=frames_ref,
            imu6=imu6,
            task=task,
        )

    def _build_frame_payload(self, job: _FrameJob) -> dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("call start_session() first")
        pose = identity_pose_xyzw()
        return {
            "action": "frame",
            "sessionId": self.session_id,
            "frameIndex": job.frame_index,
            "timestampNs": job.timestamp_ns,
            "task": job.task,
            "observationState": job.imu6.reshape(OBS_STATE_DIM).astype(float).tolist(),
            "observationPose": pose.astype(float).tolist(),
            "observationHands": [0.0] * OBS_HANDS_DIM,
            "actionVector": [0.0],
        }

    def _send_frame_job(self, job: _FrameJob) -> dict[str, Any]:
        payload = self._build_frame_payload(job)
        files: dict[str, tuple[str, bytes, str]] = {}
        for key in ALL_LEROBOT_VIDEO_KEYS:
            rgb = job.camera_frames.get(key)
            if rgb is None:
                continue
            safe_name = key.replace(".", "_") + ".jpg"
            files[key] = (safe_name, self._encode_rgb_jpeg(rgb), "image/jpeg")
        boundary = f"----datalab{uuid.uuid4().hex}"
        body = self._encode_multipart(
            fields={"payload": json.dumps(payload, separators=(",", ":"))},
            files=files,
            boundary=boundary,
        )
        return self._request(
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def start_upload_worker(self) -> None:
        if self._upload_thread is not None and self._upload_thread.is_alive():
            return
        self._upload_thread = threading.Thread(
            target=self._upload_worker_loop,
            name="datalab-frame-upload",
            daemon=True,
        )
        self._upload_thread.start()

    def enqueue_frame(
        self,
        *,
        frame_index: int,
        timestamp_ns: int,
        camera_frames: dict[str, np.ndarray],
        imu6: np.ndarray,
        task: str,
    ) -> None:
        if not self.session_id:
            raise RuntimeError("call start_session() first")
        if self._upload_thread is None or not self._upload_thread.is_alive():
            raise RuntimeError("call start_upload_worker() before enqueue_frame()")
        job = self._snapshot_frame_job(
            frame_index=frame_index,
            timestamp_ns=timestamp_ns,
            camera_frames=camera_frames,
            imu6=imu6,
            task=task,
        )
        try:
            self._upload_queue.put_nowait(job)
        except queue.Full:
            try:
                self._upload_queue.get_nowait()
                with self._stats_lock:
                    self._dropped_frames += 1
                    dropped = self._dropped_frames
                    queued = self._upload_queue.qsize()
                self._log(
                    "queue_drop",
                    frame_index=frame_index,
                    dropped=dropped,
                    queued=queued,
                )
            except queue.Empty:
                pass
            self._upload_queue.put_nowait(job)
        if frame_index % 30 == 0:
            self._log(
                "enqueue",
                frame_index=frame_index,
                queued=self._upload_queue.qsize(),
            )

    def _upload_worker_loop(self) -> None:
        while True:
            from_queue = False
            if self._held_job is not None:
                job = self._held_job
            else:
                job = self._upload_queue.get()
                from_queue = True
                if job is None:
                    self._upload_queue.task_done()
                    break

            try:
                out = self._send_frame_job(job)
                self._note_upload_success()
                with self._stats_lock:
                    self._uploaded_frames += 1
                    if out.get("duplicate"):
                        self._duplicate_frames += 1
                    total = out.get("totalFrames")
                    if total is not None:
                        self._last_server_total = int(total)
                next_idx = job.frame_index + 1
                with self._stats_lock:
                    if next_idx > self.next_frame_index:
                        self.next_frame_index = next_idx
                self._persist_checkpoint(next_frame_index=next_idx, task=job.task)
                self._held_job = None
                if from_queue:
                    self._upload_queue.task_done()
                if job.frame_index % 30 == 0 or out.get("duplicate"):
                    self._log(
                        "upload_ok",
                        frame_index=job.frame_index,
                        duplicate=bool(out.get("duplicate")),
                        server_total=out.get("totalFrames"),
                        queued=self._upload_queue.qsize(),
                    )
            except Exception as exc:
                self._note_upload_failure(exc)
                if self._should_retry_upload(exc):
                    self._held_job = job
                    delay = self._retry_backoff_s
                    self._log(
                        "upload_retry",
                        frame_index=job.frame_index,
                        backoff_s=round(delay, 2),
                        err=str(exc)[:160],
                    )
                    self._retry_sleep()
                else:
                    self._held_job = None
                    if from_queue:
                        self._upload_queue.task_done()

    def stop_upload_worker(self, *, drain: bool = True, timeout_s: float = 120.0) -> None:
        if drain:
            deadline = time.monotonic() + timeout_s
            while self._upload_queue.unfinished_tasks > 0 or self._held_job is not None:
                if time.monotonic() >= deadline:
                    break
                if self._held_job is not None:
                    try:
                        self._send_frame_job(self._held_job)
                        self._held_job = None
                    except Exception:
                        time.sleep(0.2)
                time.sleep(0.05)
            try:
                self._upload_queue.join()
            except Exception:
                pass
        try:
            self._upload_queue.put_nowait(None)
        except queue.Full:
            try:
                self._upload_queue.get_nowait()
            except queue.Empty:
                pass
            self._upload_queue.put_nowait(None)
        thread = self._upload_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._upload_thread = None

    def upload_queue_size(self) -> int:
        return self._upload_queue.qsize()

    def upload_stats(self) -> dict[str, Any]:
        with self._stats_lock:
            queued = self._upload_queue.qsize() + (1 if self._held_job is not None else 0)
            return {
                "uploaded": self._uploaded_frames,
                "duplicates": self._duplicate_frames,
                "dropped": self._dropped_frames,
                "queued": queued,
                "serverTotalFrames": self._last_server_total,
                "sessionId": self.session_id,
                "nextFrameIndex": self.next_frame_index,
                "lastError": self._last_upload_error,
                "networkOk": self._network_ok,
            }

    def push_frame(
        self,
        *,
        frame_index: int,
        timestamp_ns: int,
        camera_frames: dict[str, np.ndarray],
        imu6: np.ndarray,
        task: str,
    ) -> dict[str, Any]:
        """Synchronous upload (encode + POST). Prefer enqueue_frame() for capture loops."""
        job = self._snapshot_frame_job(
            frame_index=frame_index,
            timestamp_ns=timestamp_ns,
            camera_frames=camera_frames,
            imu6=imu6,
            task=task,
        )
        return self._send_frame_job(job)
