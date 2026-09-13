"""MCAP segment writer for ego-mcap (Track 1: HW JPEG; Track 2: VPU H.264 CompressedVideo)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping

from mcap.writer import Writer

MCAP_SCHEMA_VERSION = 1
MCAP_SEGMENT_FILENAME = "segment.mcap"

CAMERA_TOPICS: dict[str, str] = {
    "front_left": "/ego/camera/front_left",
    "front_right": "/ego/camera/front_right",
    "rear_left": "/ego/camera/rear_left",
    "rear_right": "/ego/camera/rear_right",
}

TOPIC_SESSION_META = "/ego/session_meta"
TOPIC_IMU_RAW = "/ego/imu/raw"
TOPIC_OBS_STATE = "/ego/observation/state"
TOPIC_TASK = "/ego/task"

_COMPRESSED_IMAGE_JSONSCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "timestamp": {
                "type": "object",
                "properties": {
                    "sec": {"type": "integer"},
                    "nsec": {"type": "integer"},
                },
            },
            "frame_id": {"type": "string"},
            "format": {"type": "string"},
            "data": {"type": "string", "contentEncoding": "base64"},
        },
    }
).encode("utf-8")

_COMPRESSED_VIDEO_JSONSCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "timestamp": {
                "type": "object",
                "properties": {
                    "sec": {"type": "integer"},
                    "nsec": {"type": "integer"},
                },
            },
            "frame_id": {"type": "string"},
            "format": {"type": "string"},
            "data": {"type": "string", "contentEncoding": "base64"},
        },
    }
).encode("utf-8")

_JSON_OBJECT_SCHEMA = json.dumps({"type": "object"}).encode("utf-8")


def _ns_to_sec_nsec(timestamp_ns: int) -> tuple[int, int]:
    ts = max(0, int(timestamp_ns))
    return ts // 1_000_000_000, ts % 1_000_000_000


def mcap_video_codec_from_env() -> str:
    explicit = os.environ.get("MCAP_VIDEO_CODEC", "").strip().lower()
    if explicit in ("h264", "jpeg"):
        return explicit
    if os.environ.get("OAK_H264", "0").strip().lower() in ("1", "true", "yes"):
        return "h264"
    return "jpeg"


def normalize_camera_jpegs(camera_jpegs: Mapping[str, bytes]) -> dict[str, bytes]:
    """Map capture keys (LeRobot feature names) to MCAP camera role keys."""
    return normalize_camera_payloads(camera_jpegs)


def normalize_camera_payloads(camera_payloads: Mapping[str, bytes]) -> dict[str, bytes]:
    """Map capture keys (LeRobot feature names) to MCAP camera role keys."""
    if not camera_payloads:
        return {}
    out: dict[str, bytes] = {}
    for cam_key in CAMERA_TOPICS:
        payload = camera_payloads.get(cam_key)
        if payload:
            out[cam_key] = payload
    if out:
        return out
    lerobot_to_role: dict[str, str] = {}
    try:
        try:
            from ego_capture_studio.capture.topology import active_topology
        except ImportError:
            from topology import active_topology

        role_to_key = active_topology().get("role_to_lerobot_key") or {}
        lerobot_to_role = {str(v): str(k) for k, v in role_to_key.items()}
    except Exception:
        pass
    for input_key, payload in camera_payloads.items():
        if not payload:
            continue
        role = lerobot_to_role.get(input_key)
        if role and role in CAMERA_TOPICS:
            out[role] = payload
    if out:
        return out
    try:
        try:
            from ego_capture_studio.capture.preview_hub import PREVIEW_CAMERAS
        except ImportError:
            from preview_hub import PREVIEW_CAMERAS

        for short, aliases in PREVIEW_CAMERAS:
            if short in out:
                continue
            for alias in aliases:
                payload = camera_payloads.get(alias)
                if payload:
                    out[short] = payload
                    break
    except Exception:
        pass
    return out


def _compressed_image_payload(*, timestamp_ns: int, frame_id: str, jpeg: bytes) -> bytes:
    import base64

    sec, nsec = _ns_to_sec_nsec(timestamp_ns)
    body = {
        "timestamp": {"sec": sec, "nsec": nsec},
        "frame_id": frame_id,
        "format": "jpeg",
        "data": base64.b64encode(jpeg).decode("ascii"),
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def _compressed_video_payload(*, timestamp_ns: int, frame_id: str, h264: bytes) -> bytes:
    import base64

    sec, nsec = _ns_to_sec_nsec(timestamp_ns)
    body = {
        "timestamp": {"sec": sec, "nsec": nsec},
        "frame_id": frame_id,
        "format": "h264",
        "data": base64.b64encode(h264).decode("ascii"),
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class McapSegmentStats:
    frame_count: int
    imu_message_count: int
    camera_message_counts: dict[str, int]
    video_codec: str
    grid_min_ns: int | None = None
    grid_max_ns: int | None = None
    imu_min_ns: int | None = None
    imu_max_ns: int | None = None


class McapSegmentWriter:
    """Write one closed segment as a single MCAP file (persist thread only)."""

    def __init__(
        self,
        segment_dir: Path,
        *,
        session_id: str,
        segment_id: str,
        station_id: str,
        task: str,
        topology_id: str = "ego-standard",
        video_codec: str | None = None,
    ) -> None:
        self.segment_dir = Path(segment_dir)
        self.session_id = session_id
        self.segment_id = segment_id
        self.station_id = station_id
        self.task = task
        self.topology_id = topology_id
        codec = (video_codec or mcap_video_codec_from_env()).strip().lower()
        if codec not in ("jpeg", "h264"):
            raise ValueError(f"unsupported MCAP video_codec={codec!r}")
        self.video_codec = codec
        self._mcap_path = self.segment_dir / MCAP_SEGMENT_FILENAME
        self._fp: BinaryIO | None = None
        self._writer: Writer | None = None
        self._channels: dict[str, int] = {}
        self._frame_count = 0
        self._imu_message_count = 0
        self._camera_message_counts: dict[str, int] = {k: 0 for k in CAMERA_TOPICS}
        self._grid_min_ns: int | None = None
        self._grid_max_ns: int | None = None
        self._imu_min_ns: int | None = None
        self._imu_max_ns: int | None = None
        self._camera_ts_offset_max_ns: dict[str, int] = {}

    def _note_grid_ts(self, timestamp_ns: int) -> None:
        ts = int(timestamp_ns)
        if self._grid_min_ns is None or ts < self._grid_min_ns:
            self._grid_min_ns = ts
        if self._grid_max_ns is None or ts > self._grid_max_ns:
            self._grid_max_ns = ts

    def _note_imu_ts(self, timestamp_ns: int) -> None:
        ts = int(timestamp_ns)
        if ts <= 0:
            return
        if self._imu_min_ns is None or ts < self._imu_min_ns:
            self._imu_min_ns = ts
        if self._imu_max_ns is None or ts > self._imu_max_ns:
            self._imu_max_ns = ts

    def open(self) -> None:
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        self._fp = open(self._mcap_path, "wb")
        self._writer = Writer(self._fp)
        self._writer.start()
        self._register_channels()
        self._write_session_meta()

    def _register_schema(self, name: str, schema_bytes: bytes = _JSON_OBJECT_SCHEMA) -> int:
        assert self._writer is not None
        return self._writer.register_schema(name=name, encoding="jsonschema", data=schema_bytes)

    def _register_channel(self, topic: str, schema_name: str, *, schema_bytes: bytes = _JSON_OBJECT_SCHEMA) -> int:
        schema_id = self._register_schema(schema_name, schema_bytes)
        assert self._writer is not None
        return self._writer.register_channel(
            topic=topic,
            message_encoding="json",
            schema_id=schema_id,
            metadata={"schema_version": str(MCAP_SCHEMA_VERSION), "video_codec": self.video_codec},
        )

    def _register_channels(self) -> None:
        if self.video_codec == "h264":
            compressed_schema = "foxglove.CompressedVideo"
            schema_bytes = _COMPRESSED_VIDEO_JSONSCHEMA
        else:
            compressed_schema = "foxglove.CompressedImage"
            schema_bytes = _COMPRESSED_IMAGE_JSONSCHEMA
        for cam_key, topic in CAMERA_TOPICS.items():
            self._channels[f"camera:{cam_key}"] = self._register_channel(
                topic,
                compressed_schema,
                schema_bytes=schema_bytes,
            )
        self._channels["session_meta"] = self._register_channel(TOPIC_SESSION_META, "ego.SessionMeta")
        self._channels["imu_raw"] = self._register_channel(TOPIC_IMU_RAW, "ego.ImuRaw")
        self._channels["obs_state"] = self._register_channel(TOPIC_OBS_STATE, "ego.ObservationState")
        self._channels["task"] = self._register_channel(TOPIC_TASK, "ego.Task")

    def _add_json(self, channel_key: str, payload: Mapping[str, Any], *, log_time_ns: int) -> None:
        assert self._writer is not None
        channel_id = self._channels[channel_key]
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ts = int(log_time_ns)
        self._writer.add_message(channel_id=channel_id, log_time=ts, publish_time=ts, data=data)

    def _write_session_meta(self) -> None:
        payload = {
            "schema_version": MCAP_SCHEMA_VERSION,
            "session_id": self.session_id,
            "segment_id": self.segment_id,
            "station_id": self.station_id,
            "topology_id": self.topology_id,
            "task": self.task,
            "video_codec": self.video_codec,
            "frame_width": int(os.environ.get("OAK_DEFAULT_FRAME_WIDTH", "1280")),
            "frame_height": int(os.environ.get("OAK_DEFAULT_FRAME_HEIGHT", "720")),
            "camera_topics": CAMERA_TOPICS,
            "asset_semantics": "raw_sensors_only",
        }
        self._add_json("session_meta", payload, log_time_ns=0)

    def write_frame(
        self,
        *,
        frame_index: int,
        timestamp_ns: int,
        camera_jpegs: Mapping[str, bytes],
        row: Mapping[str, Any],
        camera_ts_offset_ns: dict[str, int] | None = None,
        primary_device_timestamp_ns: int | None = None,
    ) -> None:
        ts = int(timestamp_ns)
        self._note_grid_ts(ts)
        normalized = normalize_camera_payloads(camera_jpegs)
        if self.video_codec == "h264":
            if len(normalized) != len(CAMERA_TOPICS):
                missing = sorted(set(CAMERA_TOPICS) - set(normalized))
                raise RuntimeError(f"mcap_h264_incomplete_frame:missing={','.join(missing)}")
        for cam_key, topic in CAMERA_TOPICS.items():
            payload_bytes = normalized.get(cam_key)
            if not payload_bytes:
                continue
            assert self._writer is not None
            channel_id = self._channels[f"camera:{cam_key}"]
            if self.video_codec == "h264":
                data = _compressed_video_payload(timestamp_ns=ts, frame_id=cam_key, h264=payload_bytes)
            else:
                data = _compressed_image_payload(timestamp_ns=ts, frame_id=cam_key, jpeg=payload_bytes)
            self._writer.add_message(channel_id=channel_id, log_time=ts, publish_time=ts, data=data)
            self._camera_message_counts[cam_key] += 1

        obs_state = row.get("observation.state")
        if obs_state is not None:
            obs_payload: dict[str, Any] = {
                "frame_index": frame_index,
                "timestamp_ns": ts,
                "observation.state": obs_state,
            }
            dev_ts = row.get("primary_device_timestamp_ns")
            if dev_ts is None and primary_device_timestamp_ns is not None:
                dev_ts = primary_device_timestamp_ns
            if dev_ts is not None:
                obs_payload["primary_device_timestamp_ns"] = int(dev_ts)
            offsets = row.get("camera_ts_offset_ns")
            if isinstance(offsets, dict) and offsets:
                obs_payload["camera_ts_offset_ns"] = {
                    str(k): int(v) for k, v in offsets.items()
                }
            elif camera_ts_offset_ns:
                obs_payload["camera_ts_offset_ns"] = {
                    str(k): int(v) for k, v in camera_ts_offset_ns.items()
                }
            offset_src = offsets if isinstance(offsets, dict) and offsets else camera_ts_offset_ns
            if isinstance(offset_src, dict):
                for key, off in offset_src.items():
                    abs_off = abs(int(off))
                    prev = self._camera_ts_offset_max_ns.get(str(key), 0)
                    if abs_off > prev:
                        self._camera_ts_offset_max_ns[str(key)] = abs_off
            self._add_json("obs_state", obs_payload, log_time_ns=ts)
        task = row.get("task", self.task)
        self._add_json("task", {"frame_index": frame_index, "timestamp_ns": ts, "task": task}, log_time_ns=ts)
        self._frame_count += 1

    def append_imu_raw_records(self, records: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> None:
        for rec in records:
            ts = int(rec.get("ts_ns") or rec.get("timestamp_ns") or 0)
            self._note_imu_ts(ts)
            self._add_json("imu_raw", rec, log_time_ns=ts)
            self._imu_message_count += 1

    def close(self) -> Path:
        assert self._writer is not None
        assert self._fp is not None
        if self.video_codec == "h264" and self._frame_count > 0:
            try:
                from ego_capture_studio.capture.h264_segment_boundary import require_camera_parity
            except ImportError:
                from h264_segment_boundary import require_camera_parity

            require_camera_parity(self._camera_message_counts)
            close_meta: dict[str, Any] = {
                "schema_version": MCAP_SCHEMA_VERSION,
                "session_id": self.session_id,
                "segment_id": self.segment_id,
                "station_id": self.station_id,
                "topology_id": self.topology_id,
                "task": self.task,
                "video_codec": self.video_codec,
                "frame_count": self._frame_count,
                "camera_message_counts": dict(self._camera_message_counts),
                "closed": True,
            }
            if self._camera_ts_offset_max_ns:
                close_meta["camera_ts_offset_max_abs_ns"] = dict(self._camera_ts_offset_max_ns)
            self._add_json("session_meta", close_meta, log_time_ns=0)
        self._writer.finish()
        self._fp.close()
        self._writer = None
        self._fp = None
        return self._mcap_path

    def stats(self) -> McapSegmentStats:
        return McapSegmentStats(
            frame_count=self._frame_count,
            imu_message_count=self._imu_message_count,
            camera_message_counts=dict(self._camera_message_counts),
            video_codec=self.video_codec,
            grid_min_ns=self._grid_min_ns,
            grid_max_ns=self._grid_max_ns,
            imu_min_ns=self._imu_min_ns,
            imu_max_ns=self._imu_max_ns,
        )

    def timeline_summary(self) -> dict[str, Any]:
        try:
            from ego_capture_studio.capture.strict_fps_gate import timeline_from_writer_spans
        except ImportError:
            from strict_fps_gate import timeline_from_writer_spans

        return timeline_from_writer_spans(
            frame_count=self._frame_count,
            grid_min_ns=self._grid_min_ns,
            grid_max_ns=self._grid_max_ns,
            imu_min_ns=self._imu_min_ns,
            imu_max_ns=self._imu_max_ns,
            imu_samples=self._imu_message_count,
        )


def is_mcap_segment_enabled() -> bool:
    return os.environ.get("SEGMENT_MCAP", "0").strip().lower() in ("1", "true", "yes")


def summarize_mcap_segment(mcap_path: Path) -> dict[str, Any]:
    """Lightweight reader summary for tests and validators."""
    from mcap.reader import make_reader

    topics: dict[str, int] = {}
    with open(mcap_path, "rb") as fp:
        reader = make_reader(fp)
        for _schema, channel, _message in reader.iter_messages():
            topics[channel.topic] = topics.get(channel.topic, 0) + 1
    return {"path": str(mcap_path), "topics": topics, "message_count": sum(topics.values())}
