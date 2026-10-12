"""MCAP segment preflight validation (130 edge, before upload)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

REQUIRED_CAMERA_TOPICS = {
    "/ego/camera/front_left",
    "/ego/camera/front_right",
    "/ego/camera/rear_left",
    "/ego/camera/rear_right",
}
REQUIRED_ANY_TOPICS = {"/ego/session_meta", "/ego/imu/raw"}


class McapPreflightError(Exception):
    """Raised when MCAP preflight fails."""

    def __init__(self, result: dict) -> None:
        self.result = result
        issues = result.get("issues") or [result.get("error") or "mcap_preflight_failed"]
        super().__init__(f"MCAP preflight failed: {'; '.join(str(i) for i in issues)}")


def preflight_enabled() -> bool:
    return os.environ.get("EGO_MCAP_PREFLIGHT", "1").strip().lower() not in ("0", "false", "no")


def _decompress_if_needed(archive_path: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    if archive_path.suffix == ".zst" or archive_path.name.endswith(".mcap.zst"):
        import zstandard as zstd

        tmp = tempfile.TemporaryDirectory(prefix="ego-mcap-preflight-")
        out = Path(tmp.name) / "segment.mcap"
        dctx = zstd.ZstdDecompressor()
        with open(archive_path, "rb") as src, open(out, "wb") as dst:
            dctx.copy_stream(src, dst)
        return out, tmp
    return archive_path, None


def validate_mcap_archive(archive_path: Path) -> dict:
    """Validate MCAP archive (.mcap or .mcap.zst). Returns structured result dict."""
    from mcap.reader import make_reader

    issues: list[str] = []
    topics: dict[str, int] = {}
    session_meta: dict | None = None
    last_log_time = -1

    mcap_path, tmp = _decompress_if_needed(Path(archive_path))
    try:
        with open(mcap_path, "rb") as fp:
            reader = make_reader(fp)
            for _schema, channel, message in reader.iter_messages():
                topics[channel.topic] = topics.get(channel.topic, 0) + 1
                if message.log_time < last_log_time:
                    issues.append(f"non_monotonic_log_time:{channel.topic}")
                last_log_time = message.log_time
                if channel.topic == "/ego/session_meta" and session_meta is None:
                    try:
                        session_meta = json.loads(message.data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        issues.append("session_meta_invalid_json")
    finally:
        if tmp is not None:
            tmp.cleanup()

    missing_cams = sorted(REQUIRED_CAMERA_TOPICS - set(topics))
    if missing_cams:
        issues.append(f"missing_camera_topics:{','.join(missing_cams)}")
    for topic in REQUIRED_ANY_TOPICS:
        if topics.get(topic, 0) < 1:
            issues.append(f"missing_topic:{topic}")

    frame_count = 0
    session_id = None
    segment_id = None
    cam_counts = [topics.get(t, 0) for t in REQUIRED_CAMERA_TOPICS]
    if cam_counts and min(cam_counts) > 0 and len(set(cam_counts)) != 1:
        issues.append(f"camera_frame_mismatch:min={min(cam_counts)},max={max(cam_counts)}")
    if session_meta:
        session_id = session_meta.get("session_id")
        segment_id = session_meta.get("segment_id")
        frame_count = int(session_meta.get("frame_count") or 0)
    if frame_count <= 0 and cam_counts and min(cam_counts) > 0:
        frame_count = min(cam_counts)
    if frame_count <= 0:
        issues.append("missing_frame_count")
    if not session_id:
        issues.append("missing_session_id")
    if not segment_id:
        issues.append("missing_segment_id")

    ok = len(issues) == 0 and sum(topics.values()) > 0
    return {
        "ok": ok,
        "issues": issues,
        "topics": topics,
        "session_id": session_id,
        "segment_id": segment_id,
        "frame_count": frame_count,
        "message_count": sum(topics.values()),
    }


def preflight_segment_dir(segment_dir: Path) -> dict:
    """Validate segment.mcap inside a closed segment directory."""
    segment_dir = Path(segment_dir)
    mcap_path = segment_dir / "segment.mcap"
    if not mcap_path.is_file():
        return {"ok": False, "issues": ["missing_segment_mcap"], "segment_dir": str(segment_dir)}
    result = validate_mcap_archive(mcap_path)
    manifest_path = segment_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_frames = int(manifest.get("frame_count") or manifest.get("frames") or 0)
            mcap_frames = int(result.get("frame_count") or 0)
            if manifest_frames > 0 and mcap_frames > 0 and manifest_frames != mcap_frames:
                result.setdefault("warnings", []).append(
                    f"manifest_frame_mismatch:manifest={manifest_frames},mcap={mcap_frames}",
                )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            result.setdefault("warnings", []).append("manifest_unreadable")
    result["segment_dir"] = str(segment_dir)
    return result


def preflight_segment_dir_or_raise(segment_dir: Path) -> dict:
    if not preflight_enabled():
        return {"ok": True, "skipped": True, "segment_dir": str(segment_dir)}
    result = preflight_segment_dir(segment_dir)
    if not result.get("ok"):
        raise McapPreflightError(result)
    return result
