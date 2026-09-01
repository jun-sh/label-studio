"""Tests for MCAP segment writer (ego-mcap-pilot P1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcap_segment_writer import (
    CAMERA_TOPICS,
    McapSegmentWriter,
    TOPIC_IMU_RAW,
    mcap_video_codec_from_env,
    summarize_mcap_segment,
)


def _fake_jpeg(tag: bytes) -> bytes:
    return b"\xff\xd8\xff\xe0" + tag + b"\xff\xd9"


def test_mcap_writer_topics_and_counts(tmp_path: Path) -> None:
    writer = McapSegmentWriter(
        tmp_path,
        session_id="sess_test",
        segment_id="seg_000001",
        station_id="ego-mcap-pilot",
        task="unit-test",
    )
    writer.open()
    for i in range(3):
        ts = (i + 1) * 33_333_333
        writer.write_frame(
            frame_index=i,
            timestamp_ns=ts,
            camera_jpegs={k: _fake_jpeg(k.encode()) for k in CAMERA_TOPICS},
            row={
                "observation.state": [0.0] * 6,
                "task": "unit-test",
            },
        )
        writer.append_imu_raw_records([{"ts_ns": ts, "sensor": "gyro", "x": 0.0, "y": 0.0, "z": 0.0}])
    mcap_path = writer.close()
    assert mcap_path.is_file()

    stats = writer.stats()
    assert stats.frame_count == 3
    assert stats.imu_message_count == 3
    for cam in CAMERA_TOPICS:
        assert stats.camera_message_counts[cam] == 3

    summary = summarize_mcap_segment(mcap_path)
    topics = summary["topics"]
    assert topics["/ego/session_meta"] == 1
    assert topics[TOPIC_IMU_RAW] == 3
    for topic in CAMERA_TOPICS.values():
        assert topics[topic] == 3
    assert "/ego/observation/pose" not in topics


def test_mcap_writer_accepts_lerobot_camera_keys(tmp_path: Path) -> None:
    writer = McapSegmentWriter(
        tmp_path,
        session_id="sess_test",
        segment_id="seg_000001",
        station_id="ego-mcap-pilot",
        task="unit-test",
    )
    writer.open()
    lerobot_jpegs = {
        "observation.images.camera_front_left": _fake_jpeg(b"fl"),
        "observation.images.camera_front_right": _fake_jpeg(b"fr"),
        "observation.images.camera_rear_left": _fake_jpeg(b"rl"),
        "observation.images.camera_rear_right": _fake_jpeg(b"rr"),
    }
    writer.write_frame(
        frame_index=0,
        timestamp_ns=33_333_333,
        camera_jpegs=lerobot_jpegs,
        row={"observation.state": [0.0] * 6, "task": "unit-test"},
    )
    mcap_path = writer.close()
    summary = summarize_mcap_segment(mcap_path)
    for topic in CAMERA_TOPICS.values():
        assert summary["topics"][topic] == 1


def _fake_h264(tag: bytes) -> bytes:
    return b"\x00\x00\x00\x01" + tag + b"\x00\x00\x00\x01"


def test_mcap_writer_h264_compressed_video(tmp_path: Path) -> None:
    writer = McapSegmentWriter(
        tmp_path,
        session_id="sess_h264",
        segment_id="seg_000001",
        station_id="ego-mcap-track2",
        task="unit-test",
        video_codec="h264",
    )
    writer.open()
    for i in range(2):
        ts = (i + 1) * 33_333_333
        writer.write_frame(
            frame_index=i,
            timestamp_ns=ts,
            camera_jpegs={k: _fake_h264(k.encode()) for k in CAMERA_TOPICS},
            row={
                "observation.state": [0.0] * 6,
                "task": "unit-test",
            },
        )
    mcap_path = writer.close()
    stats = writer.stats()
    assert stats.video_codec == "h264"
    assert stats.frame_count == 2

    from mcap.reader import make_reader

    with open(mcap_path, "rb") as fp:
        reader = make_reader(fp)
        schemas = {s.id: s.name for s in reader.get_summary().schemas.values()}
        for _schema, channel, message in reader.iter_messages():
            if channel.topic.startswith("/ego/camera/"):
                assert schemas[channel.schema_id] == "foxglove.CompressedVideo"
                body = json.loads(message.data.decode("utf-8"))
                assert body["format"] == "h264"
                assert body["data"]


def test_mcap_video_codec_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCAP_VIDEO_CODEC", raising=False)
    monkeypatch.setenv("OAK_H264", "0")
    assert mcap_video_codec_from_env() == "jpeg"
    monkeypatch.setenv("OAK_H264", "1")
    assert mcap_video_codec_from_env() == "h264"
    monkeypatch.setenv("MCAP_VIDEO_CODEC", "h264")
    assert mcap_video_codec_from_env() == "h264"


def test_golden_fixture_matches_meta() -> None:
    fixture_dir = Path(__file__).resolve().parents[2] / "fixtures" / "mcap"
    mcap_path = fixture_dir / "golden-seg.mcap"
    meta_path = fixture_dir / "golden-seg.meta.json"
    if not mcap_path.is_file() or not meta_path.is_file():
        pytest.skip("golden fixture not generated yet")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    summary = summarize_mcap_segment(mcap_path)
    assert summary["topics"] == meta["summary"]["topics"]
