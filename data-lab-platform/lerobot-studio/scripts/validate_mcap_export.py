#!/usr/bin/env python3
"""PR2 acceptance: validate ROS2 MCAP export against frozen topic contract."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from mcap.reader import make_reader
    from mcap_ros2.decoder import DecoderFactory
except ImportError as exc:
    raise SystemExit("pip install mcap mcap-ros2-support") from exc

REQUIRED_TOPICS_FULL = frozenset(
    {
        "/camera/front_left/compressed",
        "/camera/front_right/compressed",
        "/camera/rear_left/compressed",
        "/camera/rear_right/compressed",
        "/sensor/imu",
        "/observation/state_imu_30hz",
        "/session/metadata",
        "/tf_static",
    }
)

TOPIC_SCHEMA = {
    "/camera/front_left/compressed": "sensor_msgs/msg/CompressedImage",
    "/camera/front_right/compressed": "sensor_msgs/msg/CompressedImage",
    "/camera/rear_left/compressed": "sensor_msgs/msg/CompressedImage",
    "/camera/rear_right/compressed": "sensor_msgs/msg/CompressedImage",
    "/sensor/imu": "sensor_msgs/msg/Imu",
    "/observation/state_imu_30hz": "std_msgs/msg/Float64MultiArray",
    "/session/metadata": "std_msgs/msg/String",
    "/tf_static": "tf2_msgs/msg/TFMessage",
}

INTRINSIC_TOPICS = frozenset(
    {
        "/camera/front_left/intrinsics",
        "/camera/front_right/intrinsics",
        "/camera/rear_left/intrinsics",
        "/camera/rear_right/intrinsics",
    }
)

for topic in INTRINSIC_TOPICS:
    TOPIC_SCHEMA[topic] = "sensor_msgs/msg/CameraInfo"


def validate_mcap(path: Path, *, expect_intrinsics: bool = False) -> dict[str, Any]:
    path = Path(path).resolve()
    errors: list[str] = []
    topic_counts: dict[str, int] = defaultdict(int)
    schema_by_topic: dict[str, str] = {}
    session_meta: dict[str, Any] | None = None
    tf_child_frames: list[str] = []
    imu_timestamps: list[int] = []

    decoder = DecoderFactory()
    with path.open("rb") as f:
        reader = make_reader(f, decoder_factories=[decoder])
        summary = reader.get_summary()
        if summary is None or not summary.statistics:
            errors.append("mcap summary/index missing")
        for schema, channel, message, ros_msg in reader.iter_decoded_messages():
            topic = channel.topic
            topic_counts[topic] += 1
            if schema is not None:
                schema_by_topic[topic] = schema.name
            expected = TOPIC_SCHEMA.get(topic)
            if expected and schema is not None and schema.name != expected:
                errors.append(f"{topic}: schema {schema.name} != {expected}")
            if topic == "/session/metadata" and ros_msg is not None:
                try:
                    session_meta = json.loads(str(ros_msg.data))
                except (json.JSONDecodeError, AttributeError):
                    errors.append("session/metadata: invalid json payload")
            if topic == "/tf_static" and ros_msg is not None:
                for tf in getattr(ros_msg, "transforms", []) or []:
                    tf_child_frames.append(str(tf.child_frame_id))
            if topic == "/sensor/imu":
                imu_timestamps.append(int(message.log_time))

    missing = REQUIRED_TOPICS_FULL - set(topic_counts)
    for topic in sorted(missing):
        errors.append(f"missing topic: {topic}")

    if expect_intrinsics:
        missing_intr = INTRINSIC_TOPICS - set(topic_counts)
        for topic in sorted(missing_intr):
            errors.append(f"missing intrinsics topic: {topic}")

    if session_meta is None:
        errors.append("session/metadata not decoded")
    else:
        if session_meta.get("calibration_status") != "pending":
            errors.append("calibration_status must be pending")
        if "imu_link" in tf_child_frames:
            errors.append("tf_static must not publish imu_link transform")
        imu_src = session_meta.get("imu_source", "")
        if imu_src not in ("imu_raw.jsonl", "rows.jsonl_30hz_degraded"):
            errors.append(f"unexpected imu_source: {imu_src}")

    validation_path = path.with_suffix(".imu_validation.json")
    validation_report: dict[str, Any] | None = None
    if validation_path.is_file():
        validation_report = json.loads(validation_path.read_text(encoding="utf-8"))
        val = validation_report.get("validation") or {}
        if validation_report.get("degraded_imu"):
            if imu_src != "rows.jsonl_30hz_degraded":
                errors.append("degraded flag mismatch between mcap meta and sidecar")
        elif val and not val.get("skipped") and val.get("ok") is False:
            errors.append(
                f"imu validation failed: gyro_rmse={val.get('gyro_rmse')} "
                f"accel_rmse={val.get('accel_rmse')}"
            )

    if imu_timestamps:
        for i in range(1, len(imu_timestamps)):
            if imu_timestamps[i] < imu_timestamps[i - 1]:
                errors.append("sensor/imu timestamps not monotonic")
                break

    return {
        "ok": len(errors) == 0,
        "mcap": str(path),
        "message_counts": dict(sorted(topic_counts.items())),
        "schemas": schema_by_topic,
        "session_metadata": session_meta,
        "tf_child_frames": tf_child_frames,
        "imu_messages": len(imu_timestamps),
        "validation_sidecar": validation_report,
        "errors": errors,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Validate MCAP export (PR2 acceptance)")
    p.add_argument("mcap", type=Path)
    p.add_argument(
        "--expect-intrinsics",
        action="store_true",
        help="Require /camera/*/intrinsics topics",
    )
    args = p.parse_args()
    report = validate_mcap(args.mcap, expect_intrinsics=args.expect_intrinsics)
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
