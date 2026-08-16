#!/usr/bin/env python3
"""Ingest imu_raw.jsonl from segment tar.zst archives into LeRobot v3 high_freq parquet."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

try:
    import zstandard as zstd
except ImportError:
    zstd = None  # type: ignore[assignment,misc]

VENDOR_SCHEMA = "datalab-ego-v1"
HIGH_FREQ_REL = "data/chunk-000/high_freq/imu_200hz.parquet"
REQUIRED_IMU_FIELDS = ("ts_ns", "sensor", "x", "y", "z")
VALID_SENSORS = frozenset({"gyro", "accel"})


def _require_zstd() -> None:
    if zstd is None:
        raise RuntimeError("zstandard package not installed")


def _load_jsonl_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid jsonl line {line_no}: {exc}") from exc
    return rows


def _read_imu_member_from_tar_zst(archive: Path) -> list[dict[str, Any]]:
    _require_zstd()
    dctx = zstd.ZstdDecompressor()
    with archive.open("rb") as raw:
        with dctx.stream_reader(raw) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    name = member.name.lstrip("./")
                    if name != "imu_raw.jsonl":
                        continue
                    extracted = tar.extractfile(member)
                    if extracted is None:
                        return []
                    text = extracted.read().decode("utf-8", errors="replace")
                    return _load_jsonl_text(text)
    return []


def _segment_id_from_tar(path: Path) -> str:
    return path.name.replace(".tar.zst", "")


def collect_imu_records(station_root: Path, session_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seg_dir = station_root / "raw" / "segments" / session_id
    if not seg_dir.is_dir():
        raise FileNotFoundError(f"no raw segments for session {session_id}")

    archives = sorted(seg_dir.glob("*.tar.zst"))
    if not archives:
        raise FileNotFoundError(f"no tar.zst under {seg_dir}")

    records: list[dict[str, Any]] = []
    imu_hz = 200
    sync_mode = "egoverse_30hz"
    segments_with_imu = 0

    for archive in archives:
        segment_id = _segment_id_from_tar(archive)
        imu_rows = _read_imu_member_from_tar_zst(archive)
        if not imu_rows:
            continue
        segments_with_imu += 1
        for rec in imu_rows:
            sensor = str(rec.get("sensor") or "")
            if sensor not in VALID_SENSORS:
                continue
            try:
                ts_ns = int(rec["ts_ns"])
                x, y, z = float(rec["x"]), float(rec["y"]), float(rec["z"])
            except (KeyError, TypeError, ValueError):
                continue
            records.append(
                {
                    "ts_ns": ts_ns,
                    "sensor": sensor,
                    "x": x,
                    "y": y,
                    "z": z,
                    "segment_id": segment_id,
                    "session_id": session_id,
                }
            )

    if not records:
        raise RuntimeError(f"no imu_raw.jsonl records in {seg_dir}")

    records.sort(key=lambda r: (int(r["ts_ns"]), str(r["sensor"])))

    meta = {
        "segments_total": len(archives),
        "segments_with_imu": segments_with_imu,
        "imu_hz_nominal": imu_hz,
        "sync_mode": sync_mode,
        "record_count": len(records),
        "ts_min_ns": int(records[0]["ts_ns"]),
        "ts_max_ns": int(records[-1]["ts_ns"]),
    }
    return records, meta


def write_imu_parquet_atomic(out_path: Path, records: list[dict[str, Any]]) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "ts_ns": pa.array([int(r["ts_ns"]) for r in records], type=pa.int64()),
            "sensor": pa.array([str(r["sensor"]) for r in records], type=pa.string()),
            "x": pa.array([float(r["x"]) for r in records], type=pa.float32()),
            "y": pa.array([float(r["y"]) for r in records], type=pa.float32()),
            "z": pa.array([float(r["z"]) for r in records], type=pa.float32()),
            "segment_id": pa.array([str(r["segment_id"]) for r in records], type=pa.string()),
            "session_id": pa.array([str(r["session_id"]) for r in records], type=pa.string()),
        }
    )
    tmp = out_path.with_suffix(f".tmp.{os.getpid()}.parquet")
    pq.write_table(table, tmp, compression="zstd")
    if pq.read_metadata(tmp).num_rows != len(records):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("imu parquet row count mismatch after write")
    tmp.replace(out_path)
    return len(records)


def merge_vendor_meta(info: dict[str, Any], station_root: Path, session_id: str, imu_meta: dict[str, Any]) -> dict[str, Any]:
    live = {}
    live_path = station_root / "live" / "session.json"
    if live_path.is_file():
        try:
            live = json.loads(live_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            live = {}

    ego_capture = info.get("ego_capture") if isinstance(info.get("ego_capture"), dict) else {}
    vendor = info.get("vendor_meta") if isinstance(info.get("vendor_meta"), dict) else {}

    vendor.update(
        {
            "schema": VENDOR_SCHEMA,
            "station_id": station_root.name,
            "device_id": str(ego_capture.get("capture_device") or station_root.name),
            "capture_batch_id": session_id,
            "imu_nominal_hz": int(imu_meta.get("imu_hz_nominal") or 200),
            "sync_mode": str(imu_meta.get("sync_mode") or "egoverse_30hz"),
            "high_freq": {
                "imu_200hz": {
                    "path": HIGH_FREQ_REL,
                    "rate_hz": int(imu_meta.get("imu_hz_nominal") or 200),
                    "sensors": ["gyro", "accel"],
                    "source": "imu_raw.jsonl",
                    "record_count": int(imu_meta.get("record_count") or 0),
                    "segments_with_imu": int(imu_meta.get("segments_with_imu") or 0),
                }
            },
            "trace_chain": vendor.get("trace_chain") or [],
        }
    )
    if live.get("sessionId"):
        vendor["active_session_id"] = str(live["sessionId"])
    info["vendor_meta"] = vendor
    return info


def ingest_imu_high_freq(station_root: Path, session_id: str) -> dict[str, Any]:
    station_root = station_root.resolve()
    info_path = station_root / "meta" / "info.json"
    if not info_path.is_file():
        raise FileNotFoundError(f"missing {info_path}")

    records, imu_meta = collect_imu_records(station_root, session_id)
    out_path = station_root / HIGH_FREQ_REL
    rows = write_imu_parquet_atomic(out_path, records)

    info = json.loads(info_path.read_text(encoding="utf-8"))
    info = merge_vendor_meta(info, station_root, session_id, imu_meta)
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    marker_path = station_root / "live" / "derive" / "imu_high_freq.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "ok": True,
        "session_id": session_id,
        "path": HIGH_FREQ_REL,
        "rows": rows,
        **imu_meta,
    }
    marker_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest imu_raw.jsonl into LeRobot high_freq parquet")
    parser.add_argument("station_root", type=Path, help="LeRobot stream station root")
    parser.add_argument("--session", required=True, help="Session id (sess_...)")
    args = parser.parse_args()

    try:
        report = ingest_imu_high_freq(args.station_root, args.session)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
