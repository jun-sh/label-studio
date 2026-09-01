"""Tests for MCAP materialize placeholder pose stripping."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_DERIVE_DIR = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("mcap_materialize", _DERIVE_DIR / "mcap-materialize.py")
_mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules["mcap_materialize"] = _mod
_SPEC.loader.exec_module(_mod)

_is_placeholder_pose = _mod._is_placeholder_pose
_is_placeholder_hands = _mod._is_placeholder_hands
materialize_mcap_archive = _mod.materialize_mcap_archive


def test_placeholder_pose_detection() -> None:
    assert _is_placeholder_pose([0.0] * 7) is True
    assert _is_placeholder_pose([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]) is True
    assert _is_placeholder_pose([0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]) is False


def test_placeholder_hands_detection() -> None:
    assert _is_placeholder_hands([0.0] * 63) is True
    assert _is_placeholder_hands([0.1] + [0.0] * 62) is False


def test_materialize_skips_legacy_pose_topic(tmp_path: Path) -> None:
    from mcap.writer import Writer

    ego_client = Path(__file__).resolve().parents[2] / "ego-stream-client"
    sys.path.insert(0, str(ego_client))
    from mcap_segment_writer import (  # noqa: E402
        CAMERA_TOPICS,
        TOPIC_SESSION_META,
        _compressed_image_payload,
    )

    TOPIC_OBS_POSE = "/ego/observation/pose"
    TOPIC_OBS_STATE = "/ego/observation/state"

    mcap_path = tmp_path / "legacy.mcap"
    with open(mcap_path, "wb") as fp:
        writer = Writer(fp)
        writer.start()
        schema_id = writer.register_schema(name="ego.SessionMeta", encoding="jsonschema", data=b"{}")
        pose_schema = writer.register_schema(name="ego.ObservationPose", encoding="jsonschema", data=b"{}")
        state_schema = writer.register_schema(name="ego.ObservationState", encoding="jsonschema", data=b"{}")
        cam_schema = writer.register_schema(
            name="foxglove.CompressedImage",
            encoding="jsonschema",
            data=b"{}",
        )
        meta_ch = writer.register_channel(
            topic=TOPIC_SESSION_META,
            message_encoding="json",
            schema_id=schema_id,
        )
        pose_ch = writer.register_channel(
            topic=TOPIC_OBS_POSE,
            message_encoding="json",
            schema_id=pose_schema,
        )
        state_ch = writer.register_channel(
            topic=TOPIC_OBS_STATE,
            message_encoding="json",
            schema_id=state_schema,
        )
        cam_channels = {}
        for topic in CAMERA_TOPICS.values():
            cam_channels[topic] = writer.register_channel(
                topic=topic,
                message_encoding="json",
                schema_id=cam_schema,
            )
        writer.add_message(
            channel_id=meta_ch,
            log_time=0,
            publish_time=0,
            data=json.dumps(
                {
                    "session_id": "sess_legacy",
                    "segment_id": "seg_000001",
                    "video_codec": "jpeg",
                    "task": "legacy",
                    "frame_count": 1,
                }
            ).encode(),
        )
        ts = 33_333_333
        writer.add_message(
            channel_id=state_ch,
            log_time=ts,
            publish_time=ts,
            data=json.dumps(
                {
                    "frame_index": 0,
                    "timestamp_ns": ts,
                    "observation.state": [0.0] * 6,
                }
            ).encode(),
        )
        writer.add_message(
            channel_id=pose_ch,
            log_time=ts,
            publish_time=ts,
            data=json.dumps(
                {
                    "frame_index": 0,
                    "timestamp_ns": ts,
                    "observation.pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                }
            ).encode(),
        )
        for topic, channel_id in cam_channels.items():
            writer.add_message(
                channel_id=channel_id,
                log_time=ts,
                publish_time=ts,
                data=_compressed_image_payload(
                    timestamp_ns=ts,
                    frame_id=topic.rsplit("/", 1)[-1],
                    jpeg=b"\xff\xd8\xff\xd9",
                ),
            )
        writer.finish()

    extract_dir = tmp_path / "extract"
    result = materialize_mcap_archive(mcap_path, extract_dir)
    assert result["ok"] is True
    rows = [
        json.loads(line)
        for line in (extract_dir / "rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    assert "observation.pose" not in rows[0]
