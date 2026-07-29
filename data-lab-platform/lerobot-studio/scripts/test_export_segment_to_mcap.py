"""Tests for export_segment_to_mcap.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from export_segment_to_mcap import export_segment_to_mcap

try:
    from mcap.reader import make_reader
except ImportError:
    make_reader = None


@pytest.fixture
def mini_segment(tmp_path: Path) -> Path:
    seg = tmp_path / "seg_000001"
    (seg / "frames").mkdir(parents=True)
    manifest = {
        "session_id": "sess_test",
        "segment_id": "seg_000001",
        "start_frame_index": 0,
        "end_frame_index": 0,
        "frame_count": 1,
    }
    (seg / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    row = {
        "frame_index": 0,
        "timestamp_ns": 1_000_000_000,
        "task": "t",
        "observation.state": [0.1, 0.2, 0.3, 0.0, 0.0, 9.8],
        "camera_ts_offset_ns": {
            "observation.images.camera_front_left": 0,
            "observation.images.camera_front_right": 1000,
            "observation.images.camera_rear_left": 2000,
            "observation.images.camera_rear_right": 3000,
        },
    }
    (seg / "rows.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    imu_lines = [
        {"ts_ns": 999_000_000, "sensor": "gyro", "x": 0.1, "y": 0.2, "z": 0.3},
        {"ts_ns": 999_000_000, "sensor": "accel", "x": 0.0, "y": 0.0, "z": 9.8},
        {"ts_ns": 1_000_000_000, "sensor": "gyro", "x": 0.1, "y": 0.2, "z": 0.3},
        {"ts_ns": 1_000_000_000, "sensor": "accel", "x": 0.0, "y": 0.0, "z": 9.8},
    ]
    (seg / "imu_raw.jsonl").write_text(
        "\n".join(json.dumps(r) for r in imu_lines) + "\n",
        encoding="utf-8",
    )
    import struct

    magic = b"DLB1"
    keys = [
        "observation.images.camera_front_left",
        "observation.images.camera_front_right",
        "observation.images.camera_rear_left",
        "observation.images.camera_rear_right",
    ]
    jpegs = {k: b"\xff\xd8\xff\xd9" for k in keys}
    buf = bytearray()
    buf.extend(struct.pack("<4sBB", magic, 1, len(keys)))
    for key in keys:
        kb = key.encode("utf-8")
        jpeg = jpegs[key]
        buf.extend(struct.pack("<H I", len(kb), len(jpeg)))
        buf.extend(kb)
        buf.extend(jpeg)
    (seg / "frames" / "00000000.bin").write_bytes(bytes(buf))
    return seg


@pytest.mark.skipif(make_reader is None, reason="mcap not installed")
def test_export_segment_to_mcap(tmp_path: Path, mini_segment: Path) -> None:
    out = tmp_path / "seg.mcap"
    report = export_segment_to_mcap(mini_segment, out, validate_imu=True)
    assert report["ok"] is True
    assert out.is_file()
    assert report["degraded_imu"] is False
    topics: set[str] = set()
    with out.open("rb") as f:
        reader = make_reader(f)
        for _schema, channel, _message in reader.iter_messages():
            topics.add(channel.topic)
    assert "/camera/front_left/compressed" in topics
    assert "/sensor/imu" in topics
    assert "/observation/state_imu_30hz" in topics
    assert "/session/metadata" in topics


def test_export_degraded_without_imu_raw(tmp_path: Path, mini_segment: Path) -> None:
    (mini_segment / "imu_raw.jsonl").unlink()
    out = tmp_path / "seg_degraded.mcap"
    report = export_segment_to_mcap(mini_segment, out, validate_imu=False)
    assert report["degraded_imu"] is True
    assert out.is_file()
