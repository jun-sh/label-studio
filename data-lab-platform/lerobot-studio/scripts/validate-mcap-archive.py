#!/usr/bin/env python3
"""Validate MCAP segment archive (.mcap or .mcap.zst) for ego-mcap-pilot ingest."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REQUIRED_CAMERA_TOPICS = {
    "/ego/camera/front_left",
    "/ego/camera/front_right",
    "/ego/camera/rear_left",
    "/ego/camera/rear_right",
}
REQUIRED_ANY_TOPICS = {"/ego/session_meta", "/ego/imu/raw"}


def _decompress_if_needed(archive_path: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    if archive_path.suffix == ".zst" or archive_path.name.endswith(".mcap.zst"):
        import zstandard as zstd

        tmp = tempfile.TemporaryDirectory(prefix="ego-mcap-validate-")
        out = Path(tmp.name) / "segment.mcap"
        dctx = zstd.ZstdDecompressor()
        with open(archive_path, "rb") as src, open(out, "wb") as dst:
            dctx.copy_stream(src, dst)
        return out, tmp
    return archive_path, None


def validate_mcap_archive(archive_path: Path) -> dict:
    from mcap.reader import make_reader

    issues: list[str] = []
    topics: dict[str, int] = {}
    session_meta: dict | None = None
    last_log_time = -1

    mcap_path, tmp = _decompress_if_needed(archive_path)
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
    if session_meta:
        session_id = session_meta.get("session_id")
        segment_id = session_meta.get("segment_id")
        frame_count = int(session_meta.get("frame_count") or 0)
    if not session_id or not segment_id:
        cam_counts = [topics.get(t, 0) for t in REQUIRED_CAMERA_TOPICS]
        if cam_counts and min(cam_counts) > 0:
            frame_count = frame_count or min(cam_counts)
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


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: validate-mcap-archive.py <path>"}))
        return 2
    archive = Path(sys.argv[1])
    if not archive.is_file():
        print(json.dumps({"ok": False, "error": f"not found: {archive}"}))
        return 2
    try:
        result = validate_mcap_archive(archive)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)[:500]}))
        return 1
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
