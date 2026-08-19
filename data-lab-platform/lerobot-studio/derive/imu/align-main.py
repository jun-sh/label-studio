#!/usr/bin/env python3
"""Nearest-neighbor IMU alignment into main table rows (Phase0 §4.3)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pyarrow.parquet as pq

SENSOR_RAW_REL = "sensor_raw/imu/chunk-000/file-000.parquet"
WARN_THRESHOLD_MS = 5.0


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{path.stat().st_ino if path.exists() else 0}")
    body = "".join(f"{json.dumps(row, ensure_ascii=False)}\n" for row in rows)
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def load_imu_series(station_root: Path) -> tuple[list[float], list[list[float]], list[list[float]]]:
    imu_path = station_root / SENSOR_RAW_REL
    if not imu_path.is_file():
        return [], [], []
    table = pq.read_table(imu_path)
    data = table.to_pydict()
    timestamps = [float(v) for v in data.get("imu_timestamp", [])]
    accels = data.get("accel", [])
    gyros = data.get("gyro", [])
    return timestamps, accels, gyros


def nearest_imu(timestamps: list[float], accels: list, gyros: list, target_sec: float) -> tuple[list[float], list[float], float, float]:
    if not timestamps:
        nan3 = [float("nan")] * 3
        return nan3, nan3, float("nan"), float("inf")
    best_idx = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - target_sec))
    accel = [float(v) for v in accels[best_idx]]
    gyro = [float(v) for v in gyros[best_idx]]
    matched = timestamps[best_idx]
    delta_ms = abs(matched - target_sec) * 1000.0
    return accel, gyro, matched, delta_ms


def align_rows(
    rows: list[dict],
    timestamps: list[float],
    accels: list,
    gyros: list,
    *,
    warn_threshold_ms: float = WARN_THRESHOLD_MS,
) -> tuple[list[dict], list[dict]]:
    aligned: list[dict] = []
    warnings: list[dict] = []
    for row in rows:
        out = dict(row)
        ts_ns = row.get("timestamp_ns")
        if ts_ns is None:
            aligned.append(out)
            continue
        target_sec = float(ts_ns) / 1e9
        accel, gyro, matched_ts, delta_ms = nearest_imu(timestamps, accels, gyros, target_sec)
        out["observation.imu_accel"] = accel
        out["observation.imu_gyro"] = gyro
        out["observation.imu_timestamp"] = [matched_ts]
        aligned.append(out)
        if delta_ms > warn_threshold_ms:
            warnings.append(
                {
                    "event": "imu_align_warn",
                    "frame_index": row.get("frame_index"),
                    "align_delta_ms": round(delta_ms, 3),
                    "threshold_ms": warn_threshold_ms,
                }
            )
    return aligned, warnings


def align_main_jsonl(station_root: Path, jsonl_path: Path) -> dict:
    rows = load_jsonl(jsonl_path)
    timestamps, accels, gyros = load_imu_series(station_root)
    aligned, warnings = align_rows(rows, timestamps, accels, gyros)
    write_jsonl(jsonl_path, aligned)
    return {
        "ok": True,
        "rows": len(aligned),
        "warnings": warnings,
        "warn_count": len(warnings),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Align IMU into main jsonl via nearest neighbor")
    parser.add_argument("station_root", type=Path)
    parser.add_argument("--jsonl", type=Path, required=True)
    args = parser.parse_args()

    try:
        report = align_main_jsonl(args.station_root.resolve(), args.jsonl.resolve())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    for warn in report.get("warnings", []):
        print(json.dumps(warn), file=sys.stderr)
    print(json.dumps({k: v for k, v in report.items() if k != "warnings"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
