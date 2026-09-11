#!/usr/bin/env python3
"""Merge unit/session IMU parquet into station sensor_raw/imu (LeRobot v3 layout)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

SENSOR_RAW_REL = Path("sensor_raw/imu/chunk-000/file-000.parquet")
EPISODE_INDEX_DEFAULT = 0
SYSTEM_FEATURE_SPECS = {
    "timestamp": {"dtype": "float32", "shape": [1], "names": None},
    "frame_index": {"dtype": "int64", "shape": [1], "names": None},
    "episode_index": {"dtype": "int64", "shape": [1], "names": None},
    "index": {"dtype": "int64", "shape": [1], "names": None},
    "task_index": {"dtype": "int64", "shape": [1], "names": None},
}


def _read_json(path: Path, default: object = None) -> object:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def update_info_sensor_raw(info_path: Path, station_root: Path, record_count: int) -> None:
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
    info["sensor_raw"] = {
        "imu": {
            "path": SENSOR_RAW_REL.as_posix(),
            "rate_hz": 200,
            "schema_version": 1,
            "sensors": ["accel", "gyro", "mag"],
            "record_count": record_count,
            "source": "imu_raw.jsonl",
        }
    }
    features = info.get("features") if isinstance(info.get("features"), dict) else {}
    for key, spec in SYSTEM_FEATURE_SPECS.items():
        features.setdefault(key, spec)
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


def _episode_map(station_root: Path) -> dict[str, int]:
    manifest = _read_json(station_root / "manifest" / "manifest.json", {})
    out: dict[str, int] = {}
    if isinstance(manifest, dict):
        for ep in manifest.get("episodes") or []:
            if isinstance(ep, dict) and ep.get("session_id") is not None:
                out[str(ep["session_id"])] = int(ep.get("episode_index", 0))
    return out


def _set_episode_index(table: pa.Table, episode_index: int) -> pa.Table:
    cols = {name: table[name] for name in table.column_names}
    cols["episode_index"] = pa.array([episode_index] * table.num_rows, type=pa.int32())
    return pa.table(cols)


def merge_unit_imu(station_root: Path, unit_imu: Path, episode_index: int) -> int:
    station_root = station_root.resolve()
    unit_imu = unit_imu.resolve()
    if not unit_imu.is_file():
        raise FileNotFoundError(f"missing unit imu parquet: {unit_imu}")
    incoming = _set_episode_index(pq.read_table(unit_imu), episode_index)
    dest = station_root / SENSOR_RAW_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        existing = pq.read_table(dest)
        merged = pa.concat_tables([existing, incoming], promote_options="default")
    else:
        merged = incoming
    tmp = dest.with_suffix(f".tmp.{dest.parent.name}.parquet")
    pq.write_table(merged, tmp, compression="zstd")
    tmp.replace(dest)
    info_path = station_root / "meta" / "info.json"
    if info_path.is_file():
        update_info_sensor_raw(info_path, station_root, merged.num_rows)
    return merged.num_rows


def rebuild_station_imu(station_root: Path) -> int:
    station_root = station_root.resolve()
    episode_map = _episode_map(station_root)
    derived = station_root / "derived"
    tables: list[pa.Table] = []
    if derived.is_dir():
        for unit_dir in sorted(derived.glob("sess_*")):
            imu_path = unit_dir / "imu.parquet"
            if not imu_path.is_file():
                continue
            ep_idx = episode_map.get(unit_dir.name, EPISODE_INDEX_DEFAULT)
            tables.append(_set_episode_index(pq.read_table(imu_path), ep_idx))
    dest = station_root / SENSOR_RAW_REL
    if dest.is_file():
        dest.unlink()
    if not tables:
        return 0
    merged = pa.concat_tables(tables, promote_options="default")
    dest.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(merged, dest, compression="zstd")
    info_path = station_root / "meta" / "info.json"
    if info_path.is_file():
        update_info_sensor_raw(info_path, station_root, merged.num_rows)
    return merged.num_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("station_root", type=Path, help="stream station root, e.g. data-storage/stream/ego-001")
    parser.add_argument("--unit-imu", type=Path, default=None, help="single unit imu.parquet to append")
    parser.add_argument("--episode-index", type=int, default=EPISODE_INDEX_DEFAULT)
    parser.add_argument("--rebuild", action="store_true", help="rebuild sensor_raw from all derived units")
    args = parser.parse_args(argv)
    if args.rebuild:
        rows = rebuild_station_imu(args.station_root)
        print(json.dumps({"ok": True, "rows": rows, "path": SENSOR_RAW_REL.as_posix()}))
        return 0
    if args.unit_imu is None:
        parser.error("--unit-imu required unless --rebuild")
    rows = merge_unit_imu(args.station_root, args.unit_imu, args.episode_index)
    print(json.dumps({"ok": True, "rows": rows, "path": SENSOR_RAW_REL.as_posix()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
