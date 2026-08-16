#!/usr/bin/env python3
"""Sync meta/vendor_annotations/ from ego annotations and update vendor_meta."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

VENDOR_SCHEMA = "datalab-ego-v1"
VENDOR_ANNOTATIONS_DIR = "meta/vendor_annotations"
SUBTASK_SEGMENTS_REL = f"{VENDOR_ANNOTATIONS_DIR}/subtask_segments.parquet"
CONTACT_PIXEL_REL = f"{VENDOR_ANNOTATIONS_DIR}/contact_pixel_per_frame.parquet"
MANIFEST_REL = f"{VENDOR_ANNOTATIONS_DIR}/manifest.json"


def _atomic_write_parquet(table: pa.Table, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.parquet")
    pq.write_table(table, tmp, compression="zstd")
    rows = pq.read_metadata(tmp).num_rows
    tmp.replace(path)
    return rows


def _load_annotations(root: Path) -> pa.Table | None:
    path = root / "meta" / "annotations.parquet"
    if not path.is_file():
        return None
    table = pq.read_table(path)
    required = {"episode_index", "frame_index", "subtask_index", "subtask_name"}
    if not required.issubset(set(table.column_names)):
        return None
    return table


def _build_subtask_segments(table: pa.Table) -> pa.Table:
    if table.num_rows == 0:
        return pa.table(
            {
                "episode_index": pa.array([], type=pa.int64()),
                "subtask_index": pa.array([], type=pa.int64()),
                "subtask_name": pa.array([], type=pa.string()),
                "start_frame": pa.array([], type=pa.int64()),
                "end_frame": pa.array([], type=pa.int64()),
                "frame_count": pa.array([], type=pa.int64()),
            }
        )

    rows = table.to_pylist()
    rows.sort(key=lambda r: (int(r["episode_index"]), int(r["frame_index"])))

    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        ep = int(row["episode_index"])
        frame = int(row["frame_index"])
        sub_idx = int(row["subtask_index"])
        sub_name = str(row.get("subtask_name") or "")
        if (
            current is None
            or current["episode_index"] != ep
            or current["subtask_index"] != sub_idx
            or current["subtask_name"] != sub_name
            or frame != current["end_frame"] + 1
        ):
            if current is not None:
                current["frame_count"] = current["end_frame"] - current["start_frame"] + 1
                segments.append(current)
            current = {
                "episode_index": ep,
                "subtask_index": sub_idx,
                "subtask_name": sub_name,
                "start_frame": frame,
                "end_frame": frame,
            }
        else:
            current["end_frame"] = frame
    if current is not None:
        current["frame_count"] = current["end_frame"] - current["start_frame"] + 1
        segments.append(current)

    return pa.table(
        {
            "episode_index": pa.array([s["episode_index"] for s in segments], type=pa.int64()),
            "subtask_index": pa.array([s["subtask_index"] for s in segments], type=pa.int64()),
            "subtask_name": pa.array([s["subtask_name"] for s in segments], type=pa.string()),
            "start_frame": pa.array([s["start_frame"] for s in segments], type=pa.int64()),
            "end_frame": pa.array([s["end_frame"] for s in segments], type=pa.int64()),
            "frame_count": pa.array([s["frame_count"] for s in segments], type=pa.int64()),
        }
    )


def _ensure_contact_pixel_skeleton(path: Path) -> int:
    if path.is_file():
        return pq.read_metadata(path).num_rows
    table = pa.table(
        {
            "episode_index": pa.array([], type=pa.int64()),
            "frame_index": pa.array([], type=pa.int64()),
            "contact_x": pa.array([], type=pa.float32()),
            "contact_y": pa.array([], type=pa.float32()),
            "contact_confidence": pa.array([], type=pa.float32()),
            "hand": pa.array([], type=pa.string()),
        }
    )
    return _atomic_write_parquet(table, path)


def merge_vendor_annotations_meta(
    info: dict[str, Any],
    *,
    subtask_rows: int,
    contact_rows: int,
    source_rows: int,
) -> dict[str, Any]:
    vendor = info.get("vendor_meta") if isinstance(info.get("vendor_meta"), dict) else {}
    vendor.setdefault("schema", VENDOR_SCHEMA)
    vendor["vendor_annotations"] = {
        "subtask_segments": {
            "path": SUBTASK_SEGMENTS_REL,
            "row_count": subtask_rows,
            "source": "meta/annotations.parquet",
            "source_rows": source_rows,
        },
        "contact_pixel_per_frame": {
            "path": CONTACT_PIXEL_REL,
            "row_count": contact_rows,
            "source": "embodied-annotate",
            "status": "ready" if contact_rows > 0 else "skeleton",
        },
    }
    info["vendor_meta"] = vendor
    return info


def sync_vendor_annotations(station_root: Path) -> dict[str, Any]:
    station_root = station_root.resolve()
    info_path = station_root / "meta" / "info.json"
    if not info_path.is_file():
        raise FileNotFoundError(f"missing {info_path}")

    out_dir = station_root / VENDOR_ANNOTATIONS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    annotations = _load_annotations(station_root)
    source_rows = int(annotations.num_rows) if annotations is not None else 0
    subtask_table = _build_subtask_segments(annotations) if annotations is not None else _build_subtask_segments(
        pa.table(
            {
                "episode_index": pa.array([], type=pa.int64()),
                "frame_index": pa.array([], type=pa.int64()),
                "subtask_index": pa.array([], type=pa.int64()),
                "subtask_name": pa.array([], type=pa.string()),
            }
        )
    )
    subtask_rows = _atomic_write_parquet(subtask_table, station_root / SUBTASK_SEGMENTS_REL)
    contact_rows = _ensure_contact_pixel_skeleton(station_root / CONTACT_PIXEL_REL)

    manifest = {
        "schema": VENDOR_SCHEMA,
        "artifacts": {
            "subtask_segments": {"path": SUBTASK_SEGMENTS_REL, "rows": subtask_rows},
            "contact_pixel_per_frame": {"path": CONTACT_PIXEL_REL, "rows": contact_rows},
        },
        "source": {
            "annotations_parquet": "meta/annotations.parquet",
            "annotations_rows": source_rows,
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    info = json.loads(info_path.read_text(encoding="utf-8"))
    info = merge_vendor_annotations_meta(
        info,
        subtask_rows=subtask_rows,
        contact_rows=contact_rows,
        source_rows=source_rows,
    )
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    marker_path = station_root / "live" / "derive" / "vendor_annotations.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "ok": True,
        "subtask_segments_rows": subtask_rows,
        "contact_pixel_rows": contact_rows,
        "annotations_source_rows": source_rows,
        "manifest": MANIFEST_REL,
    }
    marker_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync LeRobot vendor_annotations layer")
    parser.add_argument("station_root", type=Path, help="LeRobot stream station root")
    args = parser.parse_args()
    try:
        report = sync_vendor_annotations(args.station_root)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
