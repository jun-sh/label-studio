#!/usr/bin/env python3
"""PR1 acceptance: validate imu_raw.jsonl inside a segment directory or tar.zst archive."""

from __future__ import annotations

import argparse
import json
import math
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

try:
    import zstandard as zstd
except ImportError:
    zstd = None  # type: ignore[assignment]


REQUIRED_FIELDS = ("ts_ns", "sensor", "x", "y", "z")
VALID_SENSORS = frozenset({"gyro", "accel"})


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid json: {exc}") from exc
    return rows


def _extract_tar_zst(archive: Path, dest: Path) -> Path:
    if zstd is None:
        raise RuntimeError("zstandard required: pip install zstandard")
    raw = zstd.ZstdDecompressor().decompress(archive.read_bytes())
    tar_path = dest / "segment.tar"
    tar_path.write_bytes(raw)
    with tarfile.open(tar_path, "r") as tar:
        tar.extractall(dest, filter="data")
    members = [p for p in dest.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    if not members:
        raise ValueError(f"no segment root found in {archive}")
    return members[0]


def validate_imu_raw_records(
    records: list[dict[str, Any]],
    *,
    segment_duration_s: float | None = None,
    imu_hz: float = 200.0,
) -> dict[str, Any]:
    if not records:
        return {"ok": False, "reason": "empty_imu_raw"}

    errors: list[str] = []
    prev_ts: int | None = None
    gyro_count = 0
    accel_count = 0
    ts_values: list[int] = []

    for idx, rec in enumerate(records):
        missing = [f for f in REQUIRED_FIELDS if f not in rec]
        if missing:
            errors.append(f"row {idx}: missing fields {missing}")
            continue
        sensor = str(rec["sensor"])
        if sensor not in VALID_SENSORS:
            errors.append(f"row {idx}: invalid sensor {sensor!r}")
        try:
            ts = int(rec["ts_ns"])
        except (TypeError, ValueError):
            errors.append(f"row {idx}: invalid ts_ns")
            continue
        ts_values.append(ts)
        if prev_ts is not None and ts < prev_ts:
            errors.append(f"row {idx}: non-monotonic ts_ns {ts} < {prev_ts}")
        prev_ts = ts
        for axis in ("x", "y", "z"):
            try:
                float(rec[axis])
            except (TypeError, ValueError):
                errors.append(f"row {idx}: invalid {axis}")
        if sensor == "gyro":
            gyro_count += 1
        elif sensor == "accel":
            accel_count += 1

    if not ts_values:
        return {"ok": False, "reason": "no_valid_records", "errors": errors}

    span_ns = max(ts_values) - min(ts_values)
    span_s = span_ns / 1e9
    expected = segment_duration_s if segment_duration_s and segment_duration_s > 0 else span_s
    expected_samples = max(1, int(expected * imu_hz))
    min_gyro = int(expected_samples * 0.85)
    min_accel = int(expected_samples * 0.85)

    if gyro_count < min_gyro:
        errors.append(f"gyro underflow: {gyro_count} < {min_gyro} (~{imu_hz}Hz)")
    if accel_count < min_accel:
        errors.append(f"accel underflow: {accel_count} < {min_accel} (~{imu_hz}Hz)")

    return {
        "ok": len(errors) == 0,
        "records": len(records),
        "gyro_count": gyro_count,
        "accel_count": accel_count,
        "ts_min_ns": min(ts_values),
        "ts_max_ns": max(ts_values),
        "span_s": round(span_s, 3),
        "expected_min_per_sensor": min_gyro,
        "errors": errors,
    }


def validate_segment_dir(segment_dir: Path, *, imu_hz: float = 200.0) -> dict[str, Any]:
    segment_dir = Path(segment_dir).resolve()
    imu_path = segment_dir / "imu_raw.jsonl"
    if not imu_path.is_file():
        return {"ok": False, "reason": "imu_raw_missing", "path": str(imu_path)}

    manifest_path = segment_dir / "manifest.json"
    frame_count = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frame_count = int(manifest.get("frame_count") or 0)
    duration_s = (frame_count / 30.0) if frame_count and frame_count > 0 else None

    records = _load_jsonl(imu_path)
    report = validate_imu_raw_records(records, segment_duration_s=duration_s, imu_hz=imu_hz)
    report["segment_dir"] = str(segment_dir)
    report["imu_raw_path"] = str(imu_path)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Validate segment imu_raw.jsonl (PR1 acceptance)")
    p.add_argument("path", type=Path, help="Segment directory or .tar.zst archive")
    p.add_argument("--imu-hz", type=float, default=200.0)
    args = p.parse_args()
    target = args.path.resolve()

    if target.suffix == ".zst" or target.name.endswith(".tar.zst"):
        with tempfile.TemporaryDirectory(prefix="imu_raw_val_") as tmp:
            seg_dir = _extract_tar_zst(target, Path(tmp))
            report = validate_segment_dir(seg_dir, imu_hz=args.imu_hz)
            report["archive"] = str(target)
    else:
        report = validate_segment_dir(target, imu_hz=args.imu_hz)

    print(json.dumps(report, indent=2))
    if not report.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
