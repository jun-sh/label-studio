#!/usr/bin/env python3
"""Materialize MCAP (.mcap / .mcap.zst) into DLB-compatible extract dir for unit derive."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

def _bootstrap_h264_import() -> None:
    here = Path(__file__).resolve().parent
    for base in (
        here.parent.parent / "ego-stream-client",  # local: data-lab-platform/ego-stream-client
        here.parent / "ego-stream-client",  # docker: /app/ego-stream-client
    ):
        if (base / "h264_segment_boundary.py").is_file():
            path = str(base)
            if path not in sys.path:
                sys.path.insert(0, path)
            return
    raise ImportError("h264_segment_boundary not found (expected ego-stream-client on sys.path)")


_bootstrap_h264_import()
from h264_segment_boundary import contains_idr, iter_nal_units  # noqa: E402

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

# Legacy MCAP segments may contain edge placeholder pose (identity quaternion or all zeros).
_IDENTITY_POSE = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _is_placeholder_pose(pose: Any) -> bool:
    if not isinstance(pose, list) or len(pose) != 7:
        return True
    if all(float(v) == 0.0 for v in pose):
        return True
    return all(abs(float(pose[i]) - _IDENTITY_POSE[i]) < 1e-6 for i in range(7))


def _is_placeholder_hands(hands: Any) -> bool:
    if not isinstance(hands, list) or not hands:
        return True
    return all(float(v) == 0.0 for v in hands)


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
    fmt, data = _decode_camera_message(payload)
    if fmt != "jpeg":
        raise ValueError(f"expected jpeg camera message, got format={fmt}")
    return data


def _decode_camera_message(payload: bytes) -> tuple[str, bytes]:
    body = json.loads(payload.decode("utf-8"))
    fmt = str(body.get("format") or "jpeg").strip().lower()
    data = body.get("data")
    if not data:
        raise ValueError("camera message missing data")
    return fmt, base64.b64decode(data)


def _frame_bin_name(frame_index: int) -> str:
    return f"{int(frame_index):08d}.jpg"


def _h264_trim_to_idr_enabled() -> bool:
    return os.environ.get("EGO_H264_TRIM_TO_IDR", "1").strip().lower() not in ("0", "false", "no")


def _first_idr_packet_index(packets: list[tuple[int, bytes]]) -> int | None:
    for idx, (_ts, raw) in enumerate(packets):
        if contains_idr(raw):
            return idx
    return None


def _idr_parameter_prefix(idr_access_unit: bytes) -> bytes:
    """SPS/PPS/IDR prefix from an IDR access unit for prepending to dependent P-frames."""
    prefix = bytearray()
    for nal in iter_nal_units(idr_access_unit):
        if len(nal) < 5:
            continue
        start_len = 3 if nal[0:3] == b"\x00\x00\x01" else 4
        ntype = nal[start_len] & 0x1F
        if ntype in (5, 7, 8):
            prefix.extend(nal)
    return bytes(prefix)


def _ensure_decodable_packet(idr_access_unit: bytes, payload: bytes) -> bytes:
    """Return payload unchanged.

    Prepending SPS/PPS/IDR to dependent P-frames does not restore the reference
    chain and can duplicate images / reset decoder state. Segments that start on
    a non-IDR packet must retain leading reference frames, be decoded fully then
    time-cropped, or be re-encoded — not synthetically prefixed.
    """
    return payload


def trim_h264_camera_streams(
    camera_h264: dict[str, list[tuple[int, bytes]]],
    rows: list[dict[str, Any]],
) -> tuple[dict[str, list[tuple[int, bytes]]], list[dict[str, Any]], dict[str, Any]]:
    """Align all cameras to shared global packet index (max first IDR across cameras).

    Output frame i uses source packet index align_skip + i on every camera so content
    stays synchronous. When that packet is not an IDR, prepend SPS/PPS/IDR from the
    camera's first IDR access unit so the bitstream remains decodable.
    """
    sorted_items = {
        cam: sorted(items, key=lambda item: item[0]) for cam, items in camera_h264.items()
    }
    first_idr: dict[str, int] = {}
    for cam, items in sorted_items.items():
        if not items:
            raise RuntimeError(f"h264_trim_empty_stream:camera={cam}")
        idx = _first_idr_packet_index(items)
        if idx is None:
            raise RuntimeError(f"h264_trim_no_idr:camera={cam}")
        first_idr[cam] = idx

    align_skip = max(first_idr.values())
    frame_count = min(len(sorted_items[cam]) - align_skip for cam in sorted_items)
    if frame_count <= 0:
        raise RuntimeError("h264_trim_no_frames_after_align")

    out_streams: dict[str, list[tuple[int, bytes]]] = {}
    decode_warmup_packets: dict[str, int] = {}
    for cam, items in sorted_items.items():
        # Keep reference packets between this camera's first IDR and the global
        # align index so dependent P-frames at align_skip remain decodable.
        warmup = items[first_idr[cam] : align_skip]
        content = items[align_skip : align_skip + frame_count]
        decode_warmup_packets[cam] = len(warmup)
        out_streams[cam] = [(ts, raw) for ts, raw in warmup + content]

    if len(rows) >= align_skip + frame_count:
        out_rows = [dict(row) for row in rows[align_skip : align_skip + frame_count]]
    elif len(rows) >= frame_count:
        out_rows = [dict(row) for row in rows[-frame_count:]]
    else:
        raise RuntimeError(
            f"h264_trim_rows_short:rows={len(rows)} need>={align_skip + frame_count}"
        )

    for i, row in enumerate(out_rows):
        row["frame_index"] = i

    meta = {
        "h264_trim_to_idr": True,
        "trim_align_skip_rows": align_skip,
        "trim_content_packet_index": align_skip,
        "trim_first_idr_index": first_idr,
        "trim_frames_dropped": {cam: align_skip for cam in first_idr},
        "decode_warmup_packets": decode_warmup_packets,
        "frame_count_before_trim": min(len(sorted_items[c]) for c in sorted_items),
        "frame_count_after_trim": frame_count,
    }
    return out_streams, out_rows, meta


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
        "video_codec": str((session_meta or {}).get("video_codec") or "jpeg").strip().lower(),
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
    camera_h264: dict[str, list[tuple[int, bytes]]] = {k: [] for k in CAMERA_TOPICS.values()}

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
                        fmt, raw = _decode_camera_message(message.data)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if fmt == "h264":
                        camera_h264[cam_key].append((int(message.log_time), raw))
                    else:
                        camera_jpegs[cam_key].append((int(message.log_time), raw))
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
                    dev_ts = payload.get("primary_device_timestamp_ns")
                    if dev_ts is not None:
                        row["primary_device_timestamp_ns"] = int(dev_ts)
                    if payload.get("observation.state") is not None:
                        row["observation.state"] = payload["observation.state"]
                elif topic == TOPIC_OBS_POSE:
                    frame_index = int(payload.get("frame_index", -1))
                    if frame_index < 0:
                        continue
                    pose = payload.get("observation.pose")
                    if pose is None or _is_placeholder_pose(pose):
                        continue
                    row = rows_by_index.setdefault(frame_index, {"frame_index": frame_index})
                    row["timestamp_ns"] = int(payload.get("timestamp_ns") or message.log_time)
                    row["observation.pose"] = pose
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

    has_h264_samples = any(camera_h264[k] for k in CAMERA_TOPICS.values())
    video_codec = str((session_meta or {}).get("video_codec") or "jpeg").strip().lower()
    if has_h264_samples:
        video_codec = "h264"

    if session_meta and not has_h264_samples:
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
    if not rows and video_codec == "h264":
        h264_counts = [len(camera_h264[k]) for k in CAMERA_TOPICS.values()]
        if h264_counts and all(c > 0 for c in h264_counts):
            frame_count = min(h264_counts)
            default_task = (session_meta or {}).get("task") or "unknown"
            for i in range(frame_count):
                rows_by_index[i] = {
                    "frame_index": i,
                    "timestamp_ns": (i + 1) * 33_333_333,
                    "task": default_task,
                    "observation.state": [0.0] * 6,
                }
            rows = [rows_by_index[i] for i in sorted(rows_by_index)]

    if not rows:
        raise RuntimeError(f"no frames materialized from {archive_path}")

    default_task = (session_meta or {}).get("task") or "unknown"
    for row in rows:
        row.setdefault("task", default_task)
        row.setdefault("observation.state", [0.0] * 6)
        if "observation.pose" in row and _is_placeholder_pose(row.get("observation.pose")):
            row.pop("observation.pose", None)
        if "observation.hands" in row and _is_placeholder_hands(row.get("observation.hands")):
            row.pop("observation.hands", None)

    trim_meta: dict[str, Any] | None = None
    if video_codec == "h264":
        h264_counts = [len(camera_h264[k]) for k in CAMERA_TOPICS.values()]
        if not h264_counts or not all(c > 0 for c in h264_counts):
            raise RuntimeError(f"h264 camera parity failed for {archive_path}")
        if _h264_trim_to_idr_enabled():
            camera_h264, rows, trim_meta = trim_h264_camera_streams(camera_h264, rows)
        frame_count = len(rows)
        if frame_count <= 0:
            raise RuntimeError(f"h264 trim produced no rows for {archive_path}")
        streams_dir = extract_dir / "streams"
        streams_dir.mkdir(parents=True, exist_ok=True)
        for cam_key, items in camera_h264.items():
            items.sort(key=lambda item: item[0])
            stream_path = streams_dir / f"{cam_key}.h264"
            with open(stream_path, "wb") as fp:
                for _ts, packet in items:
                    fp.write(packet)
    else:
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
        "video_codec": video_codec,
        "manifest_schema_version": 2,
    }
    if trim_meta:
        manifest["h264_trim"] = trim_meta
    device_ts = [
        int(row.get("primary_device_timestamp_ns") or row.get("timestamp_ns") or 0)
        for row in rows
    ]
    if len(device_ts) >= 2 and device_ts[-1] > device_ts[0]:
        device_span_s = (device_ts[-1] - device_ts[0]) / 1e9
        effective_fps = (len(device_ts) - 1) / device_span_s if device_span_s > 0 else 30.0
        manifest["device_timeline"] = {
            "timestamps_ns": device_ts,
            "device_span_s": device_span_s,
            "effective_fps": effective_fps,
            "interval_ns": int(device_span_s * 1e9 / max(len(device_ts) - 1, 1)),
        }
        if video_codec == "h264":
            streams_dir = extract_dir / "streams"
            streams_dir.mkdir(parents=True, exist_ok=True)
            (streams_dir / "device_timestamps.json").write_text(
                json.dumps(
                    {
                        "frame_count": len(device_ts),
                        "timestamps_ns": device_ts,
                        "device_span_s": device_span_s,
                        "effective_fps": effective_fps,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
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
        "video_codec": video_codec,
        "imu_records": len(imu_records),
        "h264_trim": trim_meta,
        "camera_frames": {
            cam_key: (
                frame_count
                if video_codec == "h264" and (extract_dir / "streams" / f"{cam_key}.h264").is_file()
                else len(list((extract_dir / "frames" / cam_key).glob("*.jpg")))
            )
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
