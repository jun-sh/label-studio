#!/usr/bin/env python3
"""Parse imu_raw.jsonl into sensor_raw/imu parquet (Phase0 §4.2)."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

SENSOR_RAW_REL = "sensor_raw/imu/chunk-000/file-000.parquet"
PAIR_TOLERANCE_NS = 500_000
EPISODE_INDEX_DEFAULT = 0


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid jsonl line {line_no}: {exc}") from exc
    return rows


def pair_imu_records(lines: list[dict[str, Any]]) -> list[tuple[int, dict[str, list[float] | None]]]:
    """Group accel/gyro by ts_ns with optional 0.5ms pairing tolerance."""
    buckets: dict[int, dict[str, list[float] | None]] = defaultdict(lambda: {"accel": None, "gyro": None})
    for rec in lines:
        sensor = str(rec.get("sensor") or "")
        if sensor not in ("accel", "gyro"):
            continue
        try:
            ts_ns = int(rec["ts_ns"])
            vec = [float(rec["x"]), float(rec["y"]), float(rec["z"])]
        except (KeyError, TypeError, ValueError):
            continue
        placed = False
        for candidate in (ts_ns, ts_ns - PAIR_TOLERANCE_NS, ts_ns + PAIR_TOLERANCE_NS):
            if buckets[candidate][sensor] is None:
                buckets[candidate][sensor] = vec
                placed = True
                break
        if not placed:
            buckets[ts_ns][sensor] = vec
    return sorted(buckets.items(), key=lambda item: item[0])


def jsonl_to_vector_rows(
    imu_lines: list[dict[str, Any]],
    *,
    segment_id: str,
    episode_index: int = EPISODE_INDEX_DEFAULT,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ts_ns, parts in pair_imu_records(imu_lines):
        accel = parts["accel"] or [float("nan")] * 3
        gyro = parts["gyro"] or [float("nan")] * 3
        rows.append(
            {
                "episode_index": episode_index,
                "segment_id": segment_id,
                "imu_timestamp": ts_ns / 1e9,
                "accel": accel,
                "gyro": gyro,
                "mag": [0.0, 0.0, 0.0],
            }
        )
    return rows


def write_rows_atomic(out_path: Path, rows: list[dict[str, Any]], *, append: bool = False) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if append and out_path.is_file():
        table_existing = pq.read_table(out_path)
        existing = table_existing.to_pylist()

    combined = existing + rows
    table = pa.table(
        {
            "episode_index": pa.array([int(r["episode_index"]) for r in combined], type=pa.int32()),
            "segment_id": pa.array([str(r["segment_id"]) for r in combined], type=pa.string()),
            "imu_timestamp": pa.array([float(r["imu_timestamp"]) for r in combined], type=pa.float64()),
            "accel": pa.array([r["accel"] for r in combined], type=pa.list_(pa.float32())),
            "gyro": pa.array([r["gyro"] for r in combined], type=pa.list_(pa.float32())),
            "mag": pa.array([r["mag"] for r in combined], type=pa.list_(pa.float32())),
        }
    )
    tmp = out_path.with_suffix(f".tmp.{os.getpid()}.parquet")
    pq.write_table(table, tmp, compression="zstd")
    if pq.read_metadata(tmp).num_rows != len(combined):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("imu parquet row count mismatch after write")
    tmp.replace(out_path)
    return len(rows)


def update_info_sensor_raw(info_path: Path, station_root: Path, record_count: int) -> None:
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
    info["sensor_raw"] = {
        "imu": {
            "path": SENSOR_RAW_REL,
            "rate_hz": 200,
            "schema_version": 1,
            "sensors": ["accel", "gyro", "mag"],
            "record_count": record_count,
            "source": "imu_raw.jsonl",
        }
    }
    features = info.get("features") if isinstance(info.get("features"), dict) else {}
    features.setdefault(
        "observation.imu_accel",
        {"dtype": "float32", "shape": [3], "names": ["x", "y", "z"]},
    )
    features.setdefault(
        "observation.imu_gyro",
        {"dtype": "float32", "shape": [3], "names": ["x", "y", "z"]},
    )
    features.setdefault(
        "observation.imu_timestamp",
        {"dtype": "float64", "shape": [1], "names": None},
    )
    info["features"] = features
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def ingest_imu_jsonl(
    station_root: Path,
    imu_jsonl: Path,
    *,
    segment_id: str,
    session_id: str,
    append: bool = True,
    episode_index: int = EPISODE_INDEX_DEFAULT,
) -> dict[str, Any]:
    lines = load_jsonl(imu_jsonl)
    rows = jsonl_to_vector_rows(lines, segment_id=segment_id, episode_index=episode_index)
    if not rows:
        raise RuntimeError(f"no imu rows parsed from {imu_jsonl}")

    out_path = station_root / SENSOR_RAW_REL
    written = write_rows_atomic(out_path, rows, append=append)
    total_rows = pq.read_metadata(out_path).num_rows if out_path.is_file() else written

    info_path = station_root / "meta" / "info.json"
    if info_path.is_file():
        update_info_sensor_raw(info_path, station_root, total_rows)

    return {
        "ok": True,
        "session_id": session_id,
        "segment_id": segment_id,
        "rows_written": written,
        "total_rows": total_rows,
        "path": SENSOR_RAW_REL,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest imu_raw.jsonl to sensor_raw parquet")
    parser.add_argument("station_root", type=Path)
    parser.add_argument("--imu-jsonl", type=Path, required=True)
    parser.add_argument("--segment-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--append", action="store_true", default=False)
    parser.add_argument("--replace", action="store_true", default=False)
    args = parser.parse_args()

    try:
        report = ingest_imu_jsonl(
            args.station_root.resolve(),
            args.imu_jsonl.resolve(),
            segment_id=args.segment_id,
            session_id=args.session_id,
            append=args.append and not args.replace,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
