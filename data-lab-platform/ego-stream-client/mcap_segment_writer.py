"""MCAP segment writer for ego-mcap-pilot (Track 1: HW JPEG CompressedImage topics)."""

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
TOPIC_OBS_POSE = "/ego/observation/pose"
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

_JSON_OBJECT_SCHEMA = json.dumps({"type": "object"}).encode("utf-8")


def _ns_to_sec_nsec(timestamp_ns: int) -> tuple[int, int]:
    ts = max(0, int(timestamp_ns))
    return ts // 1_000_000_000, ts % 1_000_000_000


def normalize_camera_jpegs(camera_jpegs: Mapping[str, bytes]) -> dict[str, bytes]:
    """Map capture keys (LeRobot feature names) to MCAP camera role keys."""
    if not camera_jpegs:
        return {}
    out: dict[str, bytes] = {}
    for cam_key in CAMERA_TOPICS:
        jpeg = camera_jpegs.get(cam_key)
        if jpeg:
            out[cam_key] = jpeg
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
    for input_key, jpeg in camera_jpegs.items():
        if not jpeg:
            continue
        role = lerobot_to_role.get(input_key)
        if role and role in CAMERA_TOPICS:
            out[role] = jpeg
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
                jpeg = camera_jpegs.get(alias)
                if jpeg:
                    out[short] = jpeg
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


@dataclass(frozen=True)
class McapSegmentStats:
    frame_count: int
    imu_message_count: int
    camera_message_counts: dict[str, int]


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
    ) -> None:
        self.segment_dir = Path(segment_dir)
        self.session_id = session_id
        self.segment_id = segment_id
        self.station_id = station_id
        self.task = task
        self.topology_id = topology_id
        self._mcap_path = self.segment_dir / MCAP_SEGMENT_FILENAME
        self._fp: BinaryIO | None = None
        self._writer: Writer | None = None
        self._channels: dict[str, int] = {}
        self._frame_count = 0
        self._imu_message_count = 0
        self._camera_message_counts: dict[str, int] = {k: 0 for k in CAMERA_TOPICS}

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
            metadata={"schema_version": str(MCAP_SCHEMA_VERSION)},
        )

    def _register_channels(self) -> None:
        compressed_schema = "foxglove.CompressedImage"
        for cam_key, topic in CAMERA_TOPICS.items():
            self._channels[f"camera:{cam_key}"] = self._register_channel(
                topic,
                compressed_schema,
                schema_bytes=_COMPRESSED_IMAGE_JSONSCHEMA,
            )
        self._channels["session_meta"] = self._register_channel(TOPIC_SESSION_META, "ego.SessionMeta")
        self._channels["imu_raw"] = self._register_channel(TOPIC_IMU_RAW, "ego.ImuRaw")
        self._channels["obs_state"] = self._register_channel(TOPIC_OBS_STATE, "ego.ObservationState")
        self._channels["obs_pose"] = self._register_channel(TOPIC_OBS_POSE, "ego.ObservationPose")
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
            "video_codec": "jpeg",
            "camera_topics": CAMERA_TOPICS,
        }
        self._add_json("session_meta", payload, log_time_ns=0)

    def write_frame(
        self,
        *,
        frame_index: int,
        timestamp_ns: int,
        camera_jpegs: Mapping[str, bytes],
        row: Mapping[str, Any],
        camera_ts_offset_ns: int | None = None,
    ) -> None:
        ts = int(timestamp_ns)
        normalized = normalize_camera_jpegs(camera_jpegs)
        for cam_key, topic in CAMERA_TOPICS.items():
            jpeg = normalized.get(cam_key)
            if not jpeg:
                continue
            assert self._writer is not None
            channel_id = self._channels[f"camera:{cam_key}"]
            data = _compressed_image_payload(timestamp_ns=ts, frame_id=cam_key, jpeg=jpeg)
            self._writer.add_message(channel_id=channel_id, log_time=ts, publish_time=ts, data=data)
            self._camera_message_counts[cam_key] += 1

        obs_state = row.get("observation.state")
        if obs_state is not None:
            self._add_json(
                "obs_state",
                {"frame_index": frame_index, "timestamp_ns": ts, "observation.state": obs_state},
                log_time_ns=ts,
            )
        obs_pose = row.get("observation.pose")
        if obs_pose is not None:
            self._add_json(
                "obs_pose",
                {"frame_index": frame_index, "timestamp_ns": ts, "observation.pose": obs_pose},
                log_time_ns=ts,
            )
        task = row.get("task", self.task)
        self._add_json("task", {"frame_index": frame_index, "timestamp_ns": ts, "task": task}, log_time_ns=ts)
        self._frame_count += 1

    def append_imu_raw_records(self, records: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> None:
        for rec in records:
            ts = int(rec.get("ts_ns") or rec.get("timestamp_ns") or 0)
            self._add_json("imu_raw", rec, log_time_ns=ts)
            self._imu_message_count += 1

    def close(self) -> Path:
        assert self._writer is not None
        assert self._fp is not None
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
