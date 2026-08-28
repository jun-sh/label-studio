#!/usr/bin/env python3
"""Materialize MCAP (.mcap / .mcap.zst) into DLB-compatible extract dir for unit derive."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

CAMERA_TOPICS: dict[str, str] = {
    "/ego/camera/front_left": "front_left",
    "/ego/camera/front_right": "front_right",
    "/ego/camera/rear_left": "rear_left",
    "/ego/camera/rear_right": "rear_right",
}
TOPIC_SESSION_META = "/ego/session_meta"
TOPIC_IMU_RAW = "/ego/imu/raw"
TOPIC_OBS_STATE = "/ego/observation/state"
TOPIC_OBS_POSE = "/ego/observation/pose"
TOPIC_TASK = "/ego/task"


def _decompress_if_needed(archive_path: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    if archive_path.suffix == ".zst" or archive_path.name.endswith(".mcap.zst"):
        import zstandard as zstd

        tmp = tempfile.TemporaryDirectory(prefix="ego-mcap-materialize-")
        out = Path(tmp.name) / "segment.mcap"
        dctx = zstd.ZstdDecompressor()
        with open(archive_path, "rb") as src, open(out, "wb") as dst:
            dctx.copy_stream(src, dst)
        return out, tmp
    return archive_path, None


def _decode_compressed_image(payload: bytes) -> bytes:
    body = json.loads(payload.decode("utf-8"))
    data = body.get("data")
    if not data:
        raise ValueError("compressed image missing data")
    return base64.b64decode(data)


def _frame_bin_name(frame_index: int) -> str:
    return f"{int(frame_index):08d}.jpg"


def summarize_mcap_archive(archive_path: Path) -> dict[str, Any]:
    from mcap.reader import make_reader

    mcap_path, tmp = _decompress_if_needed(archive_path)
    topics: dict[str, int] = {}
    session_meta: dict[str, Any] | None = None
    try:
        with open(mcap_path, "rb") as fp:
            reader = make_reader(fp)
            for _schema, channel, message in reader.iter_messages():
                topics[channel.topic] = topics.get(channel.topic, 0) + 1
                if channel.topic == TOPIC_SESSION_META and session_meta is None:
                    try:
                        session_meta = json.loads(message.data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
    finally:
        if tmp is not None:
            tmp.cleanup()

    cam_counts = [topics.get(t, 0) for t in CAMERA_TOPICS]
    frame_count = 0
    if session_meta:
        frame_count = int(session_meta.get("frame_count") or 0)
    if frame_count <= 0 and cam_counts:
        frame_count = min(c for c in cam_counts if c > 0) if any(cam_counts) else 0

    return {
        "ok": frame_count > 0,
        "frame_count": frame_count,
        "session_id": (session_meta or {}).get("session_id"),
        "segment_id": (session_meta or {}).get("segment_id"),
        "topics": topics,
        "message_count": sum(topics.values()),
    }


def extract_imu_records_from_mcap(mcap_path: Path) -> list[dict[str, Any]]:
    from mcap.reader import make_reader

    records: list[dict[str, Any]] = []
    with open(mcap_path, "rb") as fp:
        reader = make_reader(fp)
        for _schema, channel, message in reader.iter_messages():
            if channel.topic != TOPIC_IMU_RAW:
                continue
            try:
                rec = json.loads(message.data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if rec.get("sensor") not in ("accel", "gyro"):
                continue
            records.append(rec)
    records.sort(key=lambda r: int(r.get("ts_ns") or r.get("timestamp_ns") or 0))
    return records


def materialize_mcap_archive(archive_path: Path, extract_dir: Path) -> dict[str, Any]:
    from mcap.reader import make_reader

    extract_dir.mkdir(parents=True, exist_ok=True)
    mcap_path, tmp = _decompress_if_needed(archive_path)
    rows_by_index: dict[int, dict[str, Any]] = {}
    imu_records: list[dict[str, Any]] = []
    session_meta: dict[str, Any] | None = None
    camera_jpegs: dict[str, list[tuple[int, bytes]]] = {k: [] for k in CAMERA_TOPICS.values()}

    try:
        with open(mcap_path, "rb") as fp:
            reader = make_reader(fp)
            for _schema, channel, message in reader.iter_messages():
                topic = channel.topic
                if topic == TOPIC_SESSION_META and session_meta is None:
                    try:
                        session_meta = json.loads(message.data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
                    continue

                if topic == TOPIC_IMU_RAW:
                    try:
                        rec = json.loads(message.data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if rec.get("sensor") in ("accel", "gyro"):
                        imu_records.append(rec)
                    continue

                if topic in CAMERA_TOPICS:
                    cam_key = CAMERA_TOPICS[topic]
                    try:
                        jpeg = _decode_compressed_image(message.data)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    camera_jpegs[cam_key].append((int(message.log_time), jpeg))
                    continue

                try:
                    payload = json.loads(message.data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

                if topic == TOPIC_OBS_STATE:
                    frame_index = int(payload.get("frame_index", -1))
                    if frame_index < 0:
                        continue
                    row = rows_by_index.setdefault(frame_index, {"frame_index": frame_index})
                    row["timestamp_ns"] = int(payload.get("timestamp_ns") or message.log_time)
                    if payload.get("observation.state") is not None:
                        row["observation.state"] = payload["observation.state"]
                elif topic == TOPIC_OBS_POSE:
                    frame_index = int(payload.get("frame_index", -1))
                    if frame_index < 0:
                        continue
                    row = rows_by_index.setdefault(frame_index, {"frame_index": frame_index})
                    row["timestamp_ns"] = int(payload.get("timestamp_ns") or message.log_time)
                    if payload.get("observation.pose") is not None:
                        row["observation.pose"] = payload["observation.pose"]
                elif topic == TOPIC_TASK:
                    frame_index = int(payload.get("frame_index", -1))
                    if frame_index < 0:
                        continue
                    row = rows_by_index.setdefault(frame_index, {"frame_index": frame_index})
                    row["timestamp_ns"] = int(payload.get("timestamp_ns") or message.log_time)
                    if payload.get("task") is not None:
                        row["task"] = payload["task"]
    finally:
        if tmp is not None:
            tmp.cleanup()

    if session_meta:
        declared = int(session_meta.get("frame_count") or 0)
        if declared > 0:
            for i in range(declared):
                rows_by_index.setdefault(
                    i,
                    {
                        "frame_index": i,
                        "timestamp_ns": (i + 1) * 33_333_333,
                        "task": session_meta.get("task") or "unknown",
                    },
                )

    rows = [rows_by_index[i] for i in sorted(rows_by_index)]
    if not rows:
        raise RuntimeError(f"no frames materialized from {archive_path}")

    default_task = (session_meta or {}).get("task") or "unknown"
    for row in rows:
        row.setdefault("task", default_task)
        row.setdefault("observation.state", [0.0] * 6)
        row.setdefault("observation.pose", [0.0] * 7)

    for cam_key, items in camera_jpegs.items():
        items.sort(key=lambda item: item[0])
        cam_dir = extract_dir / "frames" / cam_key
        cam_dir.mkdir(parents=True, exist_ok=True)
        for idx, row in enumerate(rows):
            if idx >= len(items):
                break
            _ts, jpeg = items[idx]
            cam_dir.joinpath(_frame_bin_name(int(row["frame_index"]))).write_bytes(jpeg)

    session_id = (session_meta or {}).get("session_id") or "sess_unknown"
    segment_id = (session_meta or {}).get("segment_id") or "seg_unknown"
    frame_count = len(rows)
    start_frame_index = int(rows[0]["frame_index"]) if rows else 0

    manifest = {
        "segment_id": segment_id,
        "session_id": session_id,
        "start_frame_index": start_frame_index,
        "end_frame_index": start_frame_index + frame_count - 1,
        "frame_count": frame_count,
        "status": "CLOSED",
        "storage_format": "mcap",
        "upload_protocol": "mcap",
        "manifest_schema_version": 2,
    }
    (extract_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (extract_dir / "rows.jsonl").write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )
    imu_records.sort(key=lambda r: int(r.get("ts_ns") or r.get("timestamp_ns") or 0))
    if imu_records:
        (extract_dir / "imu_raw.jsonl").write_text(
            "\n".join(json.dumps(rec, separators=(",", ":")) for rec in imu_records) + "\n",
            encoding="utf-8",
        )

    return {
        "ok": True,
        "session_id": session_id,
        "segment_id": segment_id,
        "frame_count": frame_count,
        "imu_records": len(imu_records),
        "camera_frames": {
            cam_key: len(list((extract_dir / "frames" / cam_key).glob("*.jpg")))
            for cam_key in CAMERA_TOPICS.values()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize MCAP archive to extract dir")
    parser.add_argument("archive", type=Path)
    parser.add_argument("extract_dir", type=Path, nargs="?")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    archive = args.archive.resolve()
    if not archive.is_file():
        print(json.dumps({"ok": False, "error": f"not found: {archive}"}))
        return 2

    try:
        if args.summary_only:
            result = summarize_mcap_archive(archive)
        else:
            if not args.extract_dir:
                print(json.dumps({"ok": False, "error": "extract_dir required"}))
                return 2
            result = materialize_mcap_archive(archive, args.extract_dir.resolve())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)[:500]}))
        return 1

    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
