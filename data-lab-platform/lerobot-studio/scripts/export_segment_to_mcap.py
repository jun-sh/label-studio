#!/usr/bin/env python3
"""Export one ego segment directory to a Foxglove-playable ROS2 MCAP file."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
from mcap_ros2.writer import Writer

from ego_mcap.frame_bin import ALL_LEROBOT_VIDEO_KEYS, unpack_frame_bin
from ego_mcap.imu_align import imu6_at_timestamp
from ego_mcap.msgdefs import SCHEMAS

LEROBOT_TO_MCAP_TOPIC: dict[str, str] = {
    "observation.images.camera_front_left": "/camera/front_left/compressed",
    "observation.images.camera_front_right": "/camera/front_right/compressed",
    "observation.images.camera_rear_left": "/camera/rear_left/compressed",
    "observation.images.camera_rear_right": "/camera/rear_right/compressed",
}

LEROBOT_TO_FRAME: dict[str, str] = {
    "observation.images.camera_front_left": "camera_front_left",
    "observation.images.camera_front_right": "camera_front_right",
    "observation.images.camera_rear_left": "camera_rear_left",
    "observation.images.camera_rear_right": "camera_rear_right",
}

OAK_SOCKET_TO_LEROBOT: dict[str, str] = {
    "CAM_A": "observation.images.camera_front_left",
    "CAM_B": "observation.images.camera_front_right",
    "CAM_C": "observation.images.camera_rear_left",
    "CAM_D": "observation.images.camera_rear_right",
}

DEFAULT_EXTRINSICS = Path(__file__).resolve().parent / "ego_mcap" / "OAK-FFC-4P-ego.json"

IMU_VALIDATE_GYRO_RMSE_DPS = float(os.environ.get("DERIVE_MCAP_IMU_GYRO_RMSE_DPS", "5.0"))
IMU_VALIDATE_ACCEL_RMSE = float(os.environ.get("DERIVE_MCAP_IMU_ACCEL_RMSE", "0.5"))

UNKNOWN_COV = [-1.0] * 9


def _ns_to_stamp(ns: int) -> dict[str, int]:
    ns = int(ns)
    return {"sec": ns // 1_000_000_000, "nanosec": ns % 1_000_000_000}


def _header(seq: int, stamp_ns: int, frame_id: str) -> dict[str, Any]:
    return {"seq": int(seq), "stamp": _ns_to_stamp(stamp_ns), "frame_id": frame_id}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _load_rows(segment_dir: Path) -> list[dict[str, Any]]:
    return _load_jsonl(segment_dir / "rows.jsonl")


def _load_imu_raw(segment_dir: Path) -> list[dict[str, Any]] | None:
    path = segment_dir / "imu_raw.jsonl"
    if not path.is_file():
        return None
    return _load_jsonl(path)


def _split_imu_raw(
    records: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    gyro_ts: list[int] = []
    gyro_xyz: list[list[float]] = []
    accel_ts: list[int] = []
    accel_xyz: list[list[float]] = []
    for rec in records:
        ts = int(rec["ts_ns"])
        xyz = [float(rec["x"]), float(rec["y"]), float(rec["z"])]
        if rec.get("sensor") == "gyro":
            gyro_ts.append(ts)
            gyro_xyz.append(xyz)
        elif rec.get("sensor") == "accel":
            accel_ts.append(ts)
            accel_xyz.append(xyz)
    return (
        np.asarray(gyro_ts, dtype=np.int64),
        np.asarray(gyro_xyz, dtype=np.float32),
        np.asarray(accel_ts, dtype=np.int64),
        np.asarray(accel_xyz, dtype=np.float32),
    )


def _rpy_to_quat(roll_deg: float, pitch_deg: float, yaw_deg: float) -> tuple[float, float, float, float]:
    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    yaw = math.radians(yaw_deg)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy
    return qx, qy, qz, qw


def _load_extrinsics(cfg_path: Path | None) -> dict[str, dict[str, Any]]:
    path = cfg_path or DEFAULT_EXTRINSICS
    raw = json.loads(path.read_text(encoding="utf-8"))
    cameras = raw.get("board_config", {}).get("cameras", {})
    out: dict[str, dict[str, Any]] = {}
    for oak_socket, cam_cfg in cameras.items():
        lerobot_key = OAK_SOCKET_TO_LEROBOT.get(oak_socket)
        if not lerobot_key:
            continue
        extr = cam_cfg.get("extrinsics")
        if not extr:
            continue
        parent_oak = extr.get("to_cam", "CAM_A")
        parent_lerobot = OAK_SOCKET_TO_LEROBOT.get(parent_oak, OAK_SOCKET_TO_LEROBOT["CAM_A"])
        trans = extr.get("specTranslation", {})
        rot = extr.get("rotation", {})
        out[lerobot_key] = {
            "parent_lerobot": parent_lerobot,
            "translation_m": {
                "x": float(trans.get("x", 0.0)) / 1000.0,
                "y": float(trans.get("y", 0.0)) / 1000.0,
                "z": float(trans.get("z", 0.0)) / 1000.0,
            },
            "rpy_deg": (
                float(rot.get("r", 0.0)),
                float(rot.get("p", 0.0)),
                float(rot.get("y", 0.0)),
            ),
        }
    return out


def _camera_info_msg(
    *,
    stamp_ns: int,
    frame_id: str,
    width: int,
    height: int,
    distortion_model: str,
    coeffs: list[float],
    fx: float,
    fy: float,
    ppx: float,
    ppy: float,
) -> dict[str, Any]:
    k = [fx, 0.0, ppx, 0.0, fy, ppy, 0.0, 0.0, 1.0]
    d = list(coeffs)
    return {
        "header": _header(0, stamp_ns, frame_id),
        "height": int(height),
        "width": int(width),
        "distortion_model": distortion_model,
        "d": d,
        "k": k,
        "r": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        "p": [fx, 0.0, ppx, 0.0, fy, ppy, 0.0, 0.0, 1.0],
        "binning_x": 0,
        "binning_y": 0,
        "roi": {
            "x_offset": 0,
            "y_offset": 0,
            "height": 0,
            "width": 0,
            "do_rectify": False,
        },
    }


def _imu_msg(
    *,
    stamp_ns: int,
    frame_id: str,
    gyro: list[float],
    accel: list[float],
) -> dict[str, Any]:
    return {
        "header": _header(0, stamp_ns, frame_id),
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        "orientation_covariance": UNKNOWN_COV,
        "angular_velocity": {"x": gyro[0], "y": gyro[1], "z": gyro[2]},
        "angular_velocity_covariance": UNKNOWN_COV,
        "linear_acceleration": {"x": accel[0], "y": accel[1], "z": accel[2]},
        "linear_acceleration_covariance": UNKNOWN_COV,
    }


def _build_tf_static(
    extrinsics: dict[str, dict[str, Any]],
    stamp_ns: int,
) -> dict[str, Any]:
    transforms: list[dict[str, Any]] = []
    for child_lerobot, extr in extrinsics.items():
        parent_lerobot = extr["parent_lerobot"]
        parent_frame = LEROBOT_TO_FRAME[parent_lerobot]
        child_frame = LEROBOT_TO_FRAME[child_lerobot]
        tx = extr["translation_m"]
        qx, qy, qz, qw = _rpy_to_quat(*extr["rpy_deg"])
        transforms.append(
            {
                "header": _header(0, stamp_ns, parent_frame),
                "child_frame_id": child_frame,
                "transform": {
                    "translation": {"x": tx["x"], "y": tx["y"], "z": tx["z"]},
                    "rotation": {"x": qx, "y": qy, "z": qz, "w": qw},
                },
            }
        )
    return {"transforms": transforms}


def _validate_imu_consistency(
    rows: list[dict[str, Any]],
    gyro_ts: np.ndarray,
    gyro_xyz: np.ndarray,
    accel_ts: np.ndarray,
    accel_xyz: np.ndarray,
) -> dict[str, Any]:
    if gyro_ts.size == 0 or accel_ts.size == 0:
        return {
            "ok": True,
            "skipped": True,
            "reason": "empty_imu_raw",
        }
    gyro_err: list[float] = []
    accel_err: list[float] = []
    for row in rows:
        t_rgb_ns = int(row["timestamp_ns"])
        expected = np.asarray(row.get("observation.state", [0.0] * 6), dtype=np.float32)
        got = imu6_at_timestamp(gyro_ts, gyro_xyz, accel_ts, accel_xyz, t_rgb_ns)
        gyro_err.append(float(np.linalg.norm(got[:3] - expected[:3])))
        accel_err.append(float(np.linalg.norm(got[3:6] - expected[3:6])))
    gyro_rmse = float(np.sqrt(np.mean(np.square(gyro_err)))) if gyro_err else 0.0
    accel_rmse = float(np.sqrt(np.mean(np.square(accel_err)))) if accel_err else 0.0
    ok = gyro_rmse <= IMU_VALIDATE_GYRO_RMSE_DPS and accel_rmse <= IMU_VALIDATE_ACCEL_RMSE
    return {
        "ok": ok,
        "samples": len(rows),
        "gyro_rmse": gyro_rmse,
        "accel_rmse": accel_rmse,
        "gyro_threshold": IMU_VALIDATE_GYRO_RMSE_DPS,
        "accel_threshold": IMU_VALIDATE_ACCEL_RMSE,
    }


def _pair_imu_records(records: list[dict[str, Any]]) -> list[tuple[int, list[float], list[float]]]:
    gyro = sorted(
        (r for r in records if r.get("sensor") == "gyro"),
        key=lambda r: int(r["ts_ns"]),
    )
    accel = sorted(
        (r for r in records if r.get("sensor") == "accel"),
        key=lambda r: int(r["ts_ns"]),
    )
    paired: list[tuple[int, list[float], list[float]]] = []
    for g, a in zip(gyro, accel):
        paired.append(
            (
                int(g["ts_ns"]),
                [float(g["x"]), float(g["y"]), float(g["z"])],
                [float(a["x"]), float(a["y"]), float(a["z"])],
            )
        )
    return paired


def export_segment_to_mcap(
    segment_dir: Path,
    out_path: Path,
    *,
    intrinsics_path: Path | None = None,
    extrinsics_path: Path | None = None,
    validate_imu: bool = True,
) -> dict[str, Any]:
    segment_dir = Path(segment_dir).resolve()
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((segment_dir / "manifest.json").read_text(encoding="utf-8"))
    session_id = str(manifest.get("session_id", "unknown"))
    segment_id = str(manifest.get("segment_id", segment_dir.name))
    rows = _load_rows(segment_dir)
    imu_raw_records = _load_imu_raw(segment_dir)
    degraded_imu = imu_raw_records is None
    if degraded_imu:
        warnings.warn(
            f"WARNING: imu_raw.jsonl missing in {segment_dir}; "
            "degrading to rows.jsonl 30Hz IMU only",
            stacklevel=2,
        )

    extrinsics = _load_extrinsics(extrinsics_path)
    intrinsics_doc: dict[str, Any] | None = None
    if intrinsics_path and Path(intrinsics_path).is_file():
        intrinsics_doc = json.loads(Path(intrinsics_path).read_text(encoding="utf-8"))

    imu_source = "rows.jsonl_30hz_degraded" if degraded_imu else "imu_raw.jsonl"
    session_meta = {
        "session_id": session_id,
        "segment_id": segment_id,
        "imu_source": imu_source,
        "calibration_status": "pending",
        "notes": "camera_imu_extrinsics_not_calibrated",
    }

    gyro_ts = gyro_xyz = accel_ts = accel_xyz = np.empty((0,), dtype=np.int64)
    paired_imu: list[tuple[int, list[float], list[float]]] = []
    if imu_raw_records is not None:
        gyro_ts, gyro_xyz, accel_ts, accel_xyz = _split_imu_raw(imu_raw_records)
        paired_imu = _pair_imu_records(imu_raw_records)

    validation: dict[str, Any]
    if degraded_imu or not validate_imu:
        validation = {
            "ok": True,
            "skipped": True,
            "reason": "degraded_imu" if degraded_imu else "validate_disabled",
        }
    else:
        validation = _validate_imu_consistency(rows, gyro_ts, gyro_xyz, accel_ts, accel_xyz)

    t0 = int(rows[0]["timestamp_ns"]) if rows else 0

    registered: dict[str, Any] = {}
    with open(out_path, "wb") as mcap_fp, Writer(mcap_fp) as writer:
        for schema_name, schema_text in SCHEMAS.items():
            registered[schema_name] = writer.register_msgdef(schema_name, schema_text)

        writer.write_message(
            "/session/metadata",
            registered["std_msgs/msg/String"],
            {"data": json.dumps(session_meta)},
            log_time=t0,
            publish_time=t0,
        )
        writer.write_message(
            "/tf_static",
            registered["tf2_msgs/msg/TFMessage"],
            _build_tf_static(extrinsics, t0),
            log_time=t0,
            publish_time=t0,
        )

        if intrinsics_doc:
            cameras = intrinsics_doc.get("cameras", {})
            for lerobot_key, cam in cameras.items():
                topic_root = LEROBOT_TO_MCAP_TOPIC.get(lerobot_key)
                if not topic_root:
                    continue
                intrinsic_topic = topic_root.replace("/compressed", "/intrinsics")
                frame_id = LEROBOT_TO_FRAME[lerobot_key]
                msg = _camera_info_msg(
                    stamp_ns=t0,
                    frame_id=frame_id,
                    width=int(cam.get("width", 0)),
                    height=int(cam.get("height", 0)),
                    distortion_model=str(cam.get("distortion_model", "plumb_bob")),
                    coeffs=[float(x) for x in cam.get("coeffs", [])],
                    fx=float(cam.get("fx", 0.0)),
                    fy=float(cam.get("fy", 0.0)),
                    ppx=float(cam.get("ppx", 0.0)),
                    ppy=float(cam.get("ppy", 0.0)),
                )
                writer.write_message(
                    intrinsic_topic,
                    registered["sensor_msgs/msg/CameraInfo"],
                    msg,
                    log_time=t0,
                    publish_time=t0,
                )

        seq_by_topic: dict[str, int] = {}
        for row in rows:
            frame_index = int(row["frame_index"])
            base_ts = int(row["timestamp_ns"])
            offsets = row.get("camera_ts_offset_ns", {}) or {}
            frame_path = segment_dir / "frames" / f"{frame_index:08d}.bin"
            jpegs = unpack_frame_bin(frame_path.read_bytes()) if frame_path.is_file() else {}

            state = [float(x) for x in row.get("observation.state", [0.0] * 6)]
            writer.write_message(
                "/observation/state_imu_30hz",
                registered["std_msgs/msg/Float64MultiArray"],
                {
                    "layout": {
                        "dim": [{"label": "observation.state", "size": len(state), "stride": 1}],
                        "data_offset": 0,
                    },
                    "data": state,
                },
                log_time=base_ts,
                publish_time=base_ts,
            )

            for lerobot_key in ALL_LEROBOT_VIDEO_KEYS:
                topic = LEROBOT_TO_MCAP_TOPIC[lerobot_key]
                jpeg = jpegs.get(lerobot_key)
                if not jpeg:
                    continue
                ts = base_ts + int(offsets.get(lerobot_key, 0))
                seq = seq_by_topic.get(topic, 0)
                seq_by_topic[topic] = seq + 1
                writer.write_message(
                    topic,
                    registered["sensor_msgs/msg/CompressedImage"],
                    {
                        "header": _header(seq, ts, LEROBOT_TO_FRAME[lerobot_key]),
                        "format": "jpeg",
                        "data": list(jpeg),
                    },
                    log_time=ts,
                    publish_time=ts,
                )

        if degraded_imu:
            for row in rows:
                ts = int(row["timestamp_ns"])
                state = [float(x) for x in row.get("observation.state", [0.0] * 6)]
                writer.write_message(
                    "/sensor/imu",
                    registered["sensor_msgs/msg/Imu"],
                    _imu_msg(
                        stamp_ns=ts,
                        frame_id="imu_link",
                        gyro=state[:3],
                        accel=state[3:6],
                    ),
                    log_time=ts,
                    publish_time=ts,
                )
        else:
            for ts, gyro, accel in paired_imu:
                writer.write_message(
                    "/sensor/imu",
                    registered["sensor_msgs/msg/Imu"],
                    _imu_msg(stamp_ns=ts, frame_id="imu_link", gyro=gyro, accel=accel),
                    log_time=ts,
                    publish_time=ts,
                )

    report: dict[str, Any] = {
        "ok": True,
        "session_id": session_id,
        "segment_id": segment_id,
        "out_path": str(out_path),
        "degraded_imu": degraded_imu,
        "frame_count": len(rows),
        "imu_raw_records": len(imu_raw_records or []),
        "validation": validation,
    }
    sidecar = out_path.with_suffix(".imu_validation.json")
    sidecar.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export one ego segment directory to a Foxglove-playable ROS2 MCAP file."
    )
    parser.add_argument("segment_dir", type=Path, help="Extracted segment directory")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output .mcap path")
    parser.add_argument(
        "--intrinsics",
        type=Path,
        default=None,
        help="camera_intrinsics.json path (station meta/)",
    )
    parser.add_argument(
        "--extrinsics",
        type=Path,
        default=None,
        help="OAK board extrinsics JSON (defaults to bundled OAK-FFC-4P-ego.json)",
    )
    parser.add_argument(
        "--no-validate-imu",
        action="store_true",
        help="Skip IMU interpolation consistency validation",
    )
    args = parser.parse_args()
    report = export_segment_to_mcap(
        args.segment_dir,
        args.output,
        intrinsics_path=args.intrinsics,
        extrinsics_path=args.extrinsics,
        validate_imu=not args.no_validate_imu,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
