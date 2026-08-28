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
    GC_ELIGIBLE_STATUS,
    clear_segment_uploaded,
    flush_pending_segment_deletes,
    list_closed_pending_segments,
    manifest_status,
    mark_segment_upload_failed,
    mark_segment_uploaded,
    mark_segment_uploading,
    purge_uploaded_segments,
    read_manifest,
    segment_file_key,
)
from ego_capture_studio.capture.segment_tar_zst import pack_segment_tar_zst, parse_segment_archive_name, sha256_file
from ego_capture_studio.capture.segment_mcap import pack_segment_mcap_zst, parse_mcap_archive_name
from ego_capture_studio.capture.upload_status import UploadStatusWriter, live_ui_enabled

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
        self.session_segment_total: int | None = None
        self._http_session = requests.Session() if requests is not None else None
        if self._http_session and self.station_token:
            self._http_session.headers[STATION_TOKEN_HEADER] = self.station_token

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        out: dict[str, str] = dict(extra or {})
        if self.station_token:
            out.setdefault(STATION_TOKEN_HEADER, self.station_token)
        total = int(self.session_segment_total or 0)
        if total > 0:
            out.setdefault("X-Session-Segment-Total", str(total))
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
        if self.protocol == "mcap":
            return self._upload_segment_mcap(segment_dir)
        raise ValueError(f"unknown UPLOAD_PROTOCOL: {self.protocol!r}")

    def _upload_segment_mcap(self, segment_dir: Path) -> dict[str, Any]:
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

        mcap_path = segment_dir / "segment.mcap"
        if not mcap_path.is_file():
            raise FileNotFoundError(f"missing segment.mcap: {segment_dir}")

        cache_dir = segment_dir / ".upload"
        cache_dir.mkdir(parents=True, exist_ok=True)
        archive_path = cache_dir / f"{segment_id}.mcap.zst"
        if not archive_path.is_file():
            packed = pack_segment_mcap_zst(mcap_path, level=1)
            if packed.resolve() != archive_path.resolve():
                archive_path.write_bytes(packed.read_bytes())
        digest = sha256_file(archive_path)

        headers = self._headers(
            {
                "Content-Type": "application/zstd",
                "X-Upload-Protocol": "mcap",
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

    def upload_tar_zst_file(
        self,
        archive_path: Path,
        *,
        session_id: str,
        segment_id: str,
        seg_seq: int | None = None,
        digest: str | None = None,
    ) -> dict[str, Any]:
        """POST a pre-built seg_xxx.tar.zst (e.g. from export/ready/)."""
        archive_path = Path(archive_path).resolve()
        if not archive_path.is_file():
            raise FileNotFoundError(f"missing archive: {archive_path}")
        if seg_seq is None:
            try:
                seg_seq = int(segment_id.rsplit("_", 1)[-1])
            except ValueError:
                seg_seq = 0
        file_digest = digest or sha256_file(archive_path)
        headers = self._headers(
            {
                "Content-Type": "application/zstd",
                "X-Upload-Protocol": "tarzst",
                "X-Session-Id": session_id,
                "X-Segment-Id": segment_id,
                "X-Segment-Seq": str(seg_seq),
                "X-Content-Sha256": file_digest,
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

    def kick_derive_start(self) -> dict[str, Any]:
        """POST derive-start after batch upload (upload/derive isolation)."""
        derive_url = self.upload_url[: -len("/upload")] + "/derive-start"
        headers = self._headers()
        if self._http_session is not None:
            resp = self._http_session.post(derive_url, headers=headers, timeout=30.0)
            resp.raise_for_status()
            return self._parse_response(resp.text)
        req = urllib.request.Request(derive_url, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=30.0) as resp:
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
        try:
            UploadStatusWriter.get_default().record_quarantine_skip(
                session_id=session_id,
                segment_id=child.name,
                reason="missing_manifest",
            )
        except Exception:
            pass
        quarantined += 1
    return quarantined


def _segment_archive_bytes(segment_dir: Path) -> int:
    segment_id = segment_dir.name
    archive_path = segment_dir / ".upload" / f"{segment_id}.tar.zst"
    if archive_path.is_file():
        return archive_path.stat().st_size
    return 0


def _segment_upload_skip_reason(segment_dir: Path) -> str | None:
    try:
        manifest = read_manifest(segment_dir)
        status = manifest_status(manifest)
    except (OSError, json.JSONDecodeError, FileNotFoundError, KeyError):
        return "unreadable_manifest"
    if status == "CORRUPT":
        return "corrupt"
    if status == "RECORDING":
        return "still_recording"
    if status == "UPLOADING":
        return None
    if status not in {"CLOSED", "UPLOAD_FAILED", "UPLOADED"}:
        return f"invalid_status:{status}"
    return None


def _dedupe_pending_segment_dirs(pending: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for segment_dir in pending:
        key = str(segment_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(segment_dir)
    return out


def _prepare_segment_for_upload(segment_dir: Path, *, force: bool) -> str | None:
    """Apply --force reset and validate segment is uploadable. Returns skip reason or None."""
    skip = _segment_upload_skip_reason(segment_dir)
    if skip:
        return skip
    status = manifest_status(read_manifest(segment_dir))
    if force and status == "UPLOADED":
        clear_segment_uploaded(segment_dir)
    return None


def _upload_one_segment(
    *,
    segment_dir: Path,
    session_id: str,
    uploader: SegmentUploader,
    force: bool = False,
) -> bool:
    segment_id = segment_dir.name
    skip = _prepare_segment_for_upload(segment_dir, force=force)
    if skip:
        _log(
            "segment_skip",
            session_id=session_id,
            segment_id=segment_id,
            reason=skip,
        )
        return False

    status = UploadStatusWriter.get_default()
    last_error = ""
    for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            try:
                mark_segment_uploading(segment_dir)
            except FileNotFoundError:
                _log(
                    "segment_skip",
                    session_id=session_id,
                    segment_id=segment_id,
                    reason="vanished_manifest",
                )
                return False
            archive_bytes = _segment_archive_bytes(segment_dir)
            status.set_uploading(
                session_id=session_id,
                segment_id=segment_id,
                bytes_total=archive_bytes or 78 * 1024 * 1024,
            )
            out = uploader.upload_segment_dir(segment_dir)
            if archive_bytes <= 0:
                archive_bytes = _segment_archive_bytes(segment_dir) or 78 * 1024 * 1024
            # Defer local delete until upload_pending_segments() finishes the batch so
            # concurrent workers never race on deleted dirs / post-delete manifests.
            mark_segment_uploaded(segment_dir, delete=False)
            elapsed = time.monotonic() - t0
            duplicate = bool(out.get("duplicate"))
            manifest = read_manifest(segment_dir) if segment_dir.is_dir() else None
            _log(
                "segment_ok",
                session_id=session_id,
                segment_id=segment_id,
                frames=out.get("framesCommitted"),
                elapsed_s=round(elapsed, 2),
                duplicate=duplicate,
                protocol=uploader.protocol,
                attempt=attempt,
                manifest_status=manifest_status(manifest) if manifest else GC_ELIGIBLE_STATUS,
                remote_ack_at=((manifest or {}).get("upload") or {}).get("remote_ack_at"),
            )
            human = status.record_ok(
                session_id=session_id,
                segment_id=segment_id,
                bytes_total=archive_bytes,
                elapsed_s=elapsed,
                duplicate=duplicate,
            )
            if not live_ui_enabled():
                print(human, flush=True)
            return True
        except Exception as exc:
            last_error = str(exc)
            if attempt >= UPLOAD_MAX_RETRIES:
                mark_segment_upload_failed(segment_dir, last_error)
                try:
                    manifest = read_manifest(segment_dir)
                except FileNotFoundError:
                    manifest = {}
                _log(
                    "segment_fail",
                    session_id=session_id,
                    segment_id=segment_id,
                    err=last_error[:200],
                    attempts=int((manifest.get("upload") or {}).get("attempts") or attempt),
                    manifest_status=manifest_status(manifest) if manifest else "missing",
                )
                human = status.record_fail(
                    session_id=session_id,
                    segment_id=segment_id,
                    error=last_error,
                )
                if not live_ui_enabled():
                    print(human, flush=True)
                return False
            time.sleep(min(8.0, 2.0 ** (attempt - 1)))
    return False


def _segment_id_from_archive_name(path: Path) -> str:
    _, segment_id = parse_segment_archive_name(path.name)
    return segment_id


def _session_id_from_archive_name(path: Path) -> str:
    session_id, _ = parse_segment_archive_name(path.name)
    return session_id


def _session_from_ledger_for_archive(ready_dir: Path, archive_path: Path) -> str:
    ledger = ready_dir / "export-manifest.jsonl"
    if not ledger.is_file():
        return ""
    target_name = archive_path.name
    target_resolved = str(archive_path.resolve())
    last_sid = ""
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = str(row.get("session_id") or row.get("sessionId") or "")
        archive = str(row.get("archive") or "")
        if archive and (archive == target_resolved or archive.endswith(target_name)):
            return sid if sid.startswith("sess_") else last_sid
        if sid.startswith("sess_"):
            last_sid = sid
    return ""


def _resolve_archive_session(ready_dir: Path, archive_path: Path, fallback: str) -> str:
    sid = _session_id_from_archive_name(archive_path)
    if sid.startswith("sess_"):
        return sid
    sid = _session_from_ledger_for_archive(ready_dir, archive_path)
    if sid.startswith("sess_"):
        return sid
    return fallback.strip()


def _resolve_session_id(root: Path, hint: str = "") -> str:
    if hint.strip():
        return hint.strip()
    env_sid = os.environ.get("EGO_CAPTURE_SESSION_ID", "").strip()
    if env_sid:
        return env_sid
    reg_path = root / "registry.json"
    if reg_path.is_file():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        sessions = reg.get("sessions") or {}
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


def _session_from_export_ledger(ready_dir: Path) -> str:
    ledger = ready_dir / "export-manifest.jsonl"
    if not ledger.is_file():
        return ""
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = str(row.get("session_id") or row.get("sessionId") or "")
        if sid.startswith("sess_"):
            return sid
    return ""


def find_latest_ready_dir(export_root: Path) -> Path | None:
    ready_root = export_root / "ready"
    if not ready_root.is_dir():
        return None
    candidates: list[Path] = []
    for child in sorted(ready_root.iterdir(), reverse=True):
        if child.is_dir() and (
            any(child.glob("seg_*.tar.zst")) or any(child.glob("sess_*__seg_*.tar.zst"))
        ):
            candidates.append(child)
    return candidates[0] if candidates else None


def list_ready_archives(ready_dir: Path) -> list[Path]:
    named = sorted(ready_dir.glob("sess_*__seg_*.tar.zst"), key=lambda p: p.name)
    legacy = [p for p in sorted(ready_dir.glob("seg_*.tar.zst"), key=lambda p: p.name) if p not in named]
    return named + legacy


def _upload_one_archive(
    *,
    archive_path: Path,
    session_id: str,
    uploader: SegmentUploader,
) -> bool:
    segment_id = _segment_id_from_archive_name(archive_path)
    status = UploadStatusWriter.get_default()
    archive_bytes = archive_path.stat().st_size
    for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            status.set_uploading(
                session_id=session_id,
                segment_id=segment_id,
                bytes_total=archive_bytes,
            )
            digest = sha256_file(archive_path)
            out = uploader.upload_tar_zst_file(
                archive_path,
                session_id=session_id,
                segment_id=segment_id,
                digest=digest,
            )
            elapsed = time.monotonic() - t0
            duplicate = bool(out.get("duplicate"))
            _log(
                "segment_ok",
                session_id=session_id,
                segment_id=segment_id,
                frames=out.get("framesCommitted"),
                elapsed_s=round(elapsed, 2),
                duplicate=duplicate,
                protocol="tarzst",
                attempt=attempt,
                source="ready",
            )
            human = status.record_ok(
                session_id=session_id,
                segment_id=segment_id,
                bytes_total=archive_bytes,
                elapsed_s=elapsed,
                duplicate=duplicate,
            )
            if not live_ui_enabled():
                print(human, flush=True)
            return True
        except Exception as exc:
            if attempt >= UPLOAD_MAX_RETRIES:
                _log(
                    "segment_fail",
                    session_id=session_id,
                    segment_id=segment_id,
                    err=str(exc)[:200],
                    attempts=attempt,
                    source="ready",
                )
                human = status.record_fail(
                    session_id=session_id,
                    segment_id=segment_id,
                    error=str(exc),
                )
                if not live_ui_enabled():
                    print(human, flush=True)
                return False
            time.sleep(min(8.0, 2.0 ** (attempt - 1)))
    return False


def kick_derive_after_upload(uploader: SegmentUploader) -> dict[str, Any] | None:
    """Call 34 derive-start after batch upload; non-fatal on failure."""
    try:
        return uploader.kick_derive_start()
    except Exception as exc:
        _log("derive_start_fail", err=str(exc)[:200])
        return None


def upload_ready_archives(
    *,
    ready_dir: Path,
    session_id: str,
    uploader: SegmentUploader,
    limit: int | None = None,
    skip_init: bool = False,
) -> int:
    archives = list_ready_archives(ready_dir)
    if limit is not None:
        archives = archives[:limit]
    if not archives:
        return 0

    uploader.session_segment_total = len(archives)

    status = UploadStatusWriter.get_default()
    if not skip_init:
        status.reset_session(session_id)
        status.refresh_queue(
            session_id=session_id,
            pending=len(archives),
            skipped_segments=[],
            phase="uploading",
        )
    import sys

    meta = (
        f"开始上传 ready/：共 {len(archives)} 段 → {uploader.upload_url}\n"
        f"状态文件：{status.path}\n"
    )
    if live_ui_enabled():
        sys.stderr.write(meta)
        sys.stderr.flush()
    else:
        print(meta, end="", flush=True)

    uploaded = 0
    for archive_path in archives:
        sid = _resolve_archive_session(ready_dir, archive_path, session_id)
        if _upload_one_archive(
            archive_path=archive_path,
            session_id=sid,
            uploader=uploader,
        ):
            uploaded += 1
    last_sid = session_id
    if archives:
        last_sid = _resolve_archive_session(ready_dir, archives[-1], session_id)
    status.refresh_queue(
        session_id=last_sid,
        pending=max(0, len(archives) - uploaded),
        skipped_segments=[],
        phase="idle" if uploaded >= len(archives) else "uploading",
    )
    return uploaded


def upload_pending_segments(
    *,
    root: Path,
    session_id: str,
    uploader: SegmentUploader,
    limit: int | None = None,
    include_uploaded: bool = False,
    force: bool = False,
) -> int:
    _quarantine_orphan_segments(root, session_id)
    pending = list_closed_pending_segments(
        root,
        session_id,
        include_uploaded=include_uploaded or force,
    )
    if limit is not None:
        pending = pending[:limit]
    pending = _dedupe_pending_segment_dirs(pending)

    uploader.session_segment_total = len(pending)

    uploaded = 0
    if UPLOAD_CONCURRENCY <= 1 or len(pending) <= 1:
        for segment_dir in pending:
            if _upload_one_segment(
                segment_dir=segment_dir,
                session_id=session_id,
                uploader=uploader,
                force=force,
            ):
                uploaded += 1
    else:
        workers = min(UPLOAD_CONCURRENCY, len(pending))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _upload_one_segment,
                    segment_dir=segment_dir,
                    session_id=session_id,
                    uploader=uploader,
                    force=force,
                ): segment_dir
                for segment_dir in pending
            }
            for fut in as_completed(futures):
                if fut.result():
                    uploaded += 1
    if DELETE_AFTER_UPLOAD:
        flush_pending_segment_deletes()
        purge_uploaded_segments(root, session_id, strict=True)
    return uploaded
