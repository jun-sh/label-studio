"""Upload closed on-disk segments to Data Lab ingest (decoupled from capture)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from ego_capture_studio.capture.camera_map import ALL_LEROBOT_VIDEO_KEYS
from ego_capture_studio.capture.frame_bin_codec import unpack_frame_bin
from ego_capture_studio.capture.segment_store import (
    list_closed_pending_segments,
    mark_segment_uploaded,
    segment_file_key,
)
from ego_capture_studio.capture.segment_tar_zst import pack_segment_tar_zst, sha256_file

STATION_TOKEN_HEADER = "X-Station-Token"
DEFAULT_UPLOAD_TIMEOUT_S = float(os.environ.get("DATALAB_UPLOAD_TIMEOUT_S", "300"))
UPLOAD_PROTOCOL = os.environ.get("UPLOAD_PROTOCOL", "tarzst").strip().lower()
UPLOAD_MAX_RETRIES = max(1, int(os.environ.get("EGO_UPLOAD_MAX_RETRIES", "3")))
UPLOAD_CONCURRENCY = max(1, int(os.environ.get("EGO_UPLOAD_CONCURRENCY", "2")))
DELETE_AFTER_UPLOAD = os.environ.get("EGO_SEGMENT_DELETE_AFTER_UPLOAD", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)

_logger = logging.getLogger("datalab.segment-upload")
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)


def _log(event: str, **fields: object) -> None:
    parts = ["[datalab-segment-upload]", f"event={event}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    _logger.info(" ".join(str(p) for p in parts))


class SegmentUploader:
    def __init__(
        self,
        upload_url: str,
        *,
        timeout_s: float = DEFAULT_UPLOAD_TIMEOUT_S,
        station_token: str | None = None,
        protocol: str | None = None,
    ) -> None:
        self.upload_url = upload_url.rstrip("/")
        if not self.upload_url.endswith("/upload"):
            raise ValueError("upload_url must end with /upload")
        self.timeout_s = timeout_s
        self.protocol = (protocol or UPLOAD_PROTOCOL).strip().lower()
        self.station_token = station_token or os.environ.get("STATION_UPLOAD_TOKEN") or None
        self._http_session = requests.Session() if requests is not None else None
        if self._http_session and self.station_token:
            self._http_session.headers[STATION_TOKEN_HEADER] = self.station_token

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        out: dict[str, str] = dict(extra or {})
        if self.station_token:
            out.setdefault(STATION_TOKEN_HEADER, self.station_token)
        return out

    def _parse_response(self, raw: str) -> dict[str, Any]:
        return json.loads(raw) if raw else {}

    def start_session(
        self,
        *,
        session_id: str,
        task: str | None = None,
        video_shapes: dict[str, tuple[int, int]],
        camera_intrinsics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        shapes_out = {k: [int(h), int(w)] for k, (h, w) in video_shapes.items()}
        body: dict[str, Any] = {
            "action": "session_start",
            "sessionId": session_id,
            "videoShapes": shapes_out,
        }
        if task:
            body["task"] = task
        if camera_intrinsics:
            body["cameraIntrinsics"] = camera_intrinsics
        return self._post_json(body)

    def upload_segment_dir(self, segment_dir: Path) -> dict[str, Any]:
        if self.protocol == "multipart":
            return self._upload_segment_multipart(segment_dir)
        if self.protocol == "tarzst":
            return self._upload_segment_tarzst(segment_dir)
        raise ValueError(f"unknown UPLOAD_PROTOCOL: {self.protocol!r}")

    def _upload_segment_tarzst(self, segment_dir: Path) -> dict[str, Any]:
        segment_dir = Path(segment_dir).resolve()
        manifest_path = segment_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"missing manifest: {segment_dir}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        session_id = str(manifest["session_id"])
        segment_id = str(manifest["segment_id"])
        seg_seq = manifest.get("segment_seq")
        if seg_seq is None:
            try:
                seg_seq = int(segment_id.rsplit("_", 1)[-1])
            except ValueError:
                seg_seq = 0

        cache_dir = segment_dir / ".upload"
        cache_dir.mkdir(parents=True, exist_ok=True)
        archive_path = cache_dir / f"{segment_id}.tar.zst"
        if not archive_path.is_file():
            pack_segment_tar_zst(segment_dir, archive_path)
        digest = sha256_file(archive_path)

        headers = self._headers(
            {
                "Content-Type": "application/zstd",
                "X-Upload-Protocol": "tarzst",
                "X-Session-Id": session_id,
                "X-Segment-Id": segment_id,
                "X-Segment-Seq": str(seg_seq),
                "X-Content-Sha256": digest,
            }
        )
        if self._http_session is not None:
            with archive_path.open("rb") as body_fp:
                resp = self._http_session.post(
                    self.upload_url,
                    data=body_fp,
                    headers=headers,
                    timeout=self.timeout_s,
                )
            resp.raise_for_status()
            return self._parse_response(resp.text)

        with archive_path.open("rb") as body_fp:
            req = urllib.request.Request(
                self.upload_url,
                data=body_fp.read(),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return self._parse_response(resp.read().decode("utf-8"))

    def _upload_segment_multipart(self, segment_dir: Path) -> dict[str, Any]:
        manifest = json.loads((segment_dir / "manifest.json").read_text(encoding="utf-8"))
        session_id = str(manifest["session_id"])
        segment_id = str(manifest["segment_id"])
        rows: list[dict[str, Any]] = []
        rows_path = segment_dir / "rows.jsonl"
        if rows_path.is_file():
            for line in rows_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        frames_payload: list[dict[str, Any]] = []
        multipart_files: list[tuple[str, tuple[str, bytes, str]]] = []
        for row in rows:
            frame_index = int(row["frame_index"])
            frames_payload.append(
                {
                    "frameIndex": frame_index,
                    "timestampNs": row.get("timestamp_ns", 0),
                    "task": row.get("task", ""),
                    "observationState": row.get("observation.state", [0] * 6),
                    "observationPose": row.get("observation.pose", [0, 0, 0, 0, 0, 0, 1]),
                    "observationHands": row.get("observation.hands", [0.0] * 63),
                    "actionVector": row.get("action", [0.0]),
                }
            )
            bin_path = segment_dir / "frames" / f"{frame_index:08d}.bin"
            if bin_path.is_file():
                camera_jpegs = unpack_frame_bin(bin_path.read_bytes())
                for key in ALL_LEROBOT_VIDEO_KEYS:
                    jpeg = camera_jpegs.get(key)
                    if not jpeg:
                        continue
                    safe = key.replace(".", "_") + ".jpg"
                    file_field = segment_file_key(frame_index, key)
                    multipart_files.append(
                        (file_field, (safe, jpeg, "image/jpeg")),
                    )
            else:
                frame_dir = segment_dir / "frames" / f"{frame_index:08d}"
                for key in ALL_LEROBOT_VIDEO_KEYS:
                    safe = key.replace(".", "_") + ".jpg"
                    img_path = frame_dir / safe
                    if not img_path.is_file():
                        img_path = segment_dir / "frames" / f"{frame_index:08d}__{safe}"
                    if not img_path.is_file():
                        continue
                    file_field = segment_file_key(frame_index, key)
                    multipart_files.append(
                        (file_field, (safe, img_path.read_bytes(), "image/jpeg")),
                    )
        payload = {
            "action": "segment",
            "sessionId": session_id,
            "segmentId": segment_id,
            "startFrameIndex": int(manifest.get("start_frame_index", 0)),
            "endFrameIndex": int(manifest.get("end_frame_index", 0)),
            "frames": frames_payload,
        }
        return self._post_multipart(payload=payload, files=multipart_files)

    def _post_json(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        if self._http_session is not None:
            resp = self._http_session.post(
                self.upload_url,
                data=data,
                headers=self._headers({"Content-Type": "application/json"}),
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            return self._parse_response(resp.text)
        req = urllib.request.Request(
            self.upload_url,
            data=data,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return self._parse_response(resp.read().decode("utf-8"))

    def _post_multipart(
        self,
        *,
        payload: dict[str, Any],
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> dict[str, Any]:
        if self._http_session is not None:
            multipart: list[tuple[str, tuple[str | None, bytes | str, str]]] = [
                ("payload", (None, json.dumps(payload, separators=(",", ":")), "application/json")),
            ]
            multipart.extend(files)
            resp = self._http_session.post(
                self.upload_url,
                files=multipart,
                headers=self._headers({}),
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            return self._parse_response(resp.text)
        boundary = f"----datalab{uuid.uuid4().hex}"
        file_map = {name: (filename, data, ctype) for name, (filename, data, ctype) in files}
        body = self._encode_multipart(
            fields={"payload": json.dumps(payload, separators=(",", ":"))},
            files=file_map,
            boundary=boundary,
        )
        req = urllib.request.Request(
            self.upload_url,
            data=body,
            headers=self._headers(
                {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            ),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return self._parse_response(resp.read().decode("utf-8"))

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

    def close(self) -> None:
        if self._http_session is not None:
            self._http_session.close()


def _quarantine_orphan_segments(root: Path, session_id: str) -> int:
    """Skip segment dirs without manifest so they never block the upload queue."""
    seg_root = root / "sessions" / session_id / "segments"
    if not seg_root.is_dir():
        return 0
    quarantined = 0
    for child in sorted(seg_root.iterdir()):
        if not child.is_dir() or not child.name.startswith("seg_"):
            continue
        if (child / "manifest.json").is_file():
            continue
        skip_marker = child / ".upload_skip"
        if skip_marker.is_file():
            continue
        skip_marker.write_text(
            json.dumps({"reason": "missing_manifest", "at": time.time()}, indent=2) + "\n",
            encoding="utf-8",
        )
        _log("segment_skip", session_id=session_id, segment_id=child.name, reason="missing_manifest")
        quarantined += 1
    return quarantined


def _upload_one_segment(
    *,
    segment_dir: Path,
    session_id: str,
    uploader: SegmentUploader,
) -> bool:
    segment_id = segment_dir.name
    for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            out = uploader.upload_segment_dir(segment_dir)
            mark_segment_uploaded(segment_dir, delete=DELETE_AFTER_UPLOAD)
            elapsed = time.monotonic() - t0
            _log(
                "segment_ok",
                session_id=session_id,
                segment_id=segment_id,
                frames=out.get("framesCommitted"),
                elapsed_s=round(elapsed, 2),
                duplicate=bool(out.get("duplicate")),
                protocol=uploader.protocol,
                attempt=attempt,
            )
            return True
        except Exception as exc:
            if attempt >= UPLOAD_MAX_RETRIES:
                _log(
                    "segment_fail",
                    session_id=session_id,
                    segment_id=segment_id,
                    err=str(exc)[:200],
                    attempts=attempt,
                )
                return False
            time.sleep(min(8.0, 2.0 ** (attempt - 1)))
    return False


def upload_pending_segments(
    *,
    root: Path,
    session_id: str,
    uploader: SegmentUploader,
    limit: int | None = None,
) -> int:
    _quarantine_orphan_segments(root, session_id)
    pending = list_closed_pending_segments(root, session_id)
    if limit is not None:
        pending = pending[:limit]

    uploaded = 0
    if UPLOAD_CONCURRENCY <= 1 or len(pending) <= 1:
        for segment_dir in pending:
            if _upload_one_segment(segment_dir=segment_dir, session_id=session_id, uploader=uploader):
                uploaded += 1
        return uploaded

    workers = min(UPLOAD_CONCURRENCY, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _upload_one_segment,
                segment_dir=segment_dir,
                session_id=session_id,
                uploader=uploader,
            ): segment_dir
            for segment_dir in pending
        }
        for fut in as_completed(futures):
            if fut.result():
                uploaded += 1
    return uploaded
