#!/usr/bin/env python3
"""Generate golden-seg.mcap regression fixture (P0)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "ego-stream-client"
sys.path.insert(0, str(ROOT))

from mcap_segment_writer import McapSegmentWriter, summarize_mcap_segment  # noqa: E402

OUT = Path(__file__).resolve().parent / "golden-seg.mcap"
FRAME_COUNT = 3


def _fake_jpeg(tag: bytes) -> bytes:
    return b"\xff\xd8\xff\xe0" + tag + b"\xff\xd9"


def main() -> None:
    seg_dir = OUT.parent / "_build_golden"
    if seg_dir.exists():
        import shutil

        shutil.rmtree(seg_dir)
    seg_dir.mkdir(parents=True)

    writer = McapSegmentWriter(
        seg_dir,
        session_id="sess_golden_fixture",
        segment_id="seg_000001",
        station_id="ego-mcap-pilot",
        task="golden-fixture",
    )
    writer.open()
    for i in range(FRAME_COUNT):
        ts_ns = (i + 1) * 33_333_333
        jpegs = {
            "front_left": _fake_jpeg(b"FL" + bytes([i])),
            "front_right": _fake_jpeg(b"FR" + bytes([i])),
            "rear_left": _fake_jpeg(b"RL" + bytes([i])),
            "rear_right": _fake_jpeg(b"RR" + bytes([i])),
        }
        row = {
            "frame_index": i,
            "timestamp_ns": ts_ns,
            "task": "golden-fixture",
            "observation.state": [0.0] * 6,
            "observation.pose": [0.0] * 7,
        }
        writer.write_frame(
            frame_index=i,
            timestamp_ns=ts_ns,
            camera_jpegs=jpegs,
            row=row,
        )
        writer.append_imu_raw_records(
            [
                {"ts_ns": ts_ns, "sensor": "gyro", "x": 0.0, "y": 0.0, "z": 0.0},
                {"ts_ns": ts_ns + 1_000_000, "sensor": "accel", "x": 0.0, "y": 0.0, "z": 9.8},
            ]
        )
    mcap_path = writer.close()
    OUT.write_bytes(mcap_path.read_bytes())

    summary = summarize_mcap_segment(OUT)
    meta = {
        "fixture": "golden-seg.mcap",
        "frame_count": FRAME_COUNT,
        "schema_version": 1,
        "summary": summary,
    }
    (OUT.parent / "golden-seg.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
