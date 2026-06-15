"""Upload closed on-disk segments to Data Lab ingest (decoupled from capture)."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import numpy as np

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

STATION_TOKEN_HEADER = "X-Station-Token"
DEFAULT_UPLOAD_TIMEOUT_S = float(os.environ.get("DATALAB_UPLOAD_TIMEOUT_S", "120"))
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
    ) -> None:
        self.upload_url = upload_url.rstrip("/")
        if not self.upload_url.endswith("/upload"):
            raise ValueError("upload_url must end with /upload")
        self.timeout_s = timeout_s
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
        task: str,
        video_shapes: dict[str, tuple[int, int]],
        camera_intrinsics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        shapes_out = {k: [int(h), int(w)] for k, (h, w) in video_shapes.items()}
        body: dict[str, Any] = {
            "action": "session_start",
            "sessionId": session_id,
            "task": task,
            "videoShapes": shapes_out,
        }
        if camera_intrinsics:
            body["cameraIntrinsics"] = camera_intrinsics
        return self._post_json(body)

    def upload_segment_dir(self, segment_dir: Path) -> dict[str, Any]:
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


def upload_pending_segments(
    *,
    root: Path,
    session_id: str,
    uploader: SegmentUploader,
    limit: int | None = None,
) -> int:
    uploaded = 0
    pending = list_closed_pending_segments(root, session_id)
    for segment_dir in pending:
        if limit is not None and uploaded >= limit:
            break
        t0 = time.monotonic()
        try:
            out = uploader.upload_segment_dir(segment_dir)
            mark_segment_uploaded(segment_dir, delete=DELETE_AFTER_UPLOAD)
            elapsed = time.monotonic() - t0
            _log(
                "segment_ok",
                session_id=session_id,
                segment_id=segment_dir.name,
                frames=out.get("framesCommitted"),
                elapsed_s=round(elapsed, 2),
                duplicate=bool(out.get("duplicate")),
            )
            uploaded += 1
        except Exception as exc:
            _log(
                "segment_fail",
                session_id=session_id,
                segment_id=segment_dir.name,
                err=str(exc)[:200],
            )
            break
    return uploaded
