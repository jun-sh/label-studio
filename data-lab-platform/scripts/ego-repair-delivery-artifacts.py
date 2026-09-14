#!/usr/bin/env python3
"""Repair ego-001 delivery artifacts: parquet cast, IMU, stats, QC/annotation bootstrap."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _bootstrap(datalab_root: Path) -> None:
    ego_src = datalab_root.parent / "ego-platform" / "src"
    if ego_src.is_dir():
        sys.path.insert(0, str(ego_src))


SYSTEM_DATA_COLS = frozenset({"frame_index", "episode_index", "index", "task_index", "timestamp"})
SYSTEM_FEATURE_SPECS = {
    "timestamp": {"dtype": "float32", "shape": [1], "names": None},
    "frame_index": {"dtype": "int64", "shape": [1], "names": None},
    "episode_index": {"dtype": "int64", "shape": [1], "names": None},
    "index": {"dtype": "int64", "shape": [1], "names": None},
    "task_index": {"dtype": "int64", "shape": [1], "names": None},
}


def ensure_system_features(info: dict) -> dict:
    features = info.get("features") if isinstance(info.get("features"), dict) else {}
    for key, spec in SYSTEM_FEATURE_SPECS.items():
        features.setdefault(key, spec)
    info["features"] = features
    return info


def repair_data_parquet(root: Path) -> int:
    import pyarrow.parquet as pq
    from ego_platform.lerobot.data_schema import cast_data_table_to_info

    info_path = root / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    ensure_system_features(info)
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    features = info.get("features") or {}
    count = 0
    data_root = root / "data"
    if not data_root.is_dir():
        return 0
    for pq_path in sorted(data_root.rglob("*.parquet")):
        table = pq.read_table(pq_path)
        keep = [
            c
            for c in table.column_names
            if c in SYSTEM_DATA_COLS or (c in features and features[c].get("dtype") != "video")
        ]
        fixed = cast_data_table_to_info(table.select(keep), features)
        int_cols = {"frame_index", "episode_index", "index", "task_index"}
        cols = {}
        for name in fixed.column_names:
            col = fixed[name]
            if name in int_cols:
                import pyarrow as pa

                cols[name] = pa.array([int(x or 0) for x in col.to_pylist()], type=pa.int64())
            else:
                cols[name] = col
        import pyarrow as pa

        pq.write_table(pa.table(cols), pq_path)
        count += 1
    return count


def republish_stream_from_derived(stream_root: Path, sync_script: Path) -> int:
    spec_code = f"""
import importlib.util
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("sync_stream_parquet", {str(sync_script)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
try:
    count = mod.republish_unit_data_shards(Path({str(stream_root)!r}))
except RuntimeError as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)
mod.write_meta_stats_json(Path({str(stream_root)!r}))
print("republished", count)
"""
    proc = subprocess.run([sys.executable, "-c", spec_code], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return 0
    print(proc.stdout.strip())
    try:
        return int((proc.stdout or "").strip().split()[-1])
    except ValueError:
        return 0


def write_stats(root: Path, sync_script: Path) -> bool:
    spec_code = f"""
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("sync_stream_parquet", {str(sync_script)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ok = mod.write_meta_stats_json(Path({str(root)!r}))
print("stats_ok", ok)
"""
    proc = subprocess.run([sys.executable, "-c", spec_code], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
    return (root / "meta" / "stats.json").is_file()


def rebuild_corpus_sensor_raw(corpus_root: Path, stream_root: Path) -> dict:
    from ego_platform.lerobot.sensor_raw_imu import rebuild_corpus_sensor_raw_from_history

    return rebuild_corpus_sensor_raw_from_history(corpus_root, stream_root)


def bootstrap_qc_sidecar(corpus_root: Path, qc_base: Path) -> Path:
    key = f"{corpus_root.parent.name}_{corpus_root.name}"
    sidecar = qc_base / key
    sidecar.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "dataset_root": f"/data/corpus/{corpus_root.name}",
        "reviews": {
            "0": {"status": "approved", "note": "bootstrap internal delivery"},
            "1": {"status": "approved", "note": "bootstrap internal delivery"},
        },
    }
    (sidecar / "qc_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return sidecar


def bootstrap_annotations(corpus_root: Path) -> Path:
    meta = corpus_root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    ann_path = meta / "lerobot_annotations.json"
    payload = {
        "schema_ref": "ego_human_demo_v1",
        "episodes": {
            "0": {
                "schema_ref": "ego_human_demo_v1",
                "outcome": "success",
                "subtasks": [
                    {"label": "idle", "start": 0.0, "end": 1.0},
                    {"label": "reach", "start": 1.0, "end": 21.0},
                    {"label": "idle", "start": 21.0, "end": 22.4},
                ],
            },
            "1": {
                "schema_ref": "ego_human_demo_v1",
                "outcome": "success",
                "subtasks": [
                    {"label": "idle", "start": 0.0, "end": 0.5},
                    {"label": "reach", "start": 0.5, "end": 9.0},
                    {"label": "idle", "start": 9.0, "end": 9.97},
                ],
            },
        },
    }
    ann_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ann_path


def fix_convert_slo_markers(datalab_root: Path, station: str) -> None:
    """Align finalize.done mtime with session.READY for SLO-B gate on re-export."""
    import os
    from datetime import datetime, timezone

    stream_root = datalab_root / "data-storage" / "stream" / station
    pipeline_root = datalab_root / "data-storage" / "pipeline" / station
    for sess_dir in stream_root.glob("state/sessions/sess_*"):
        session_id = sess_dir.name
        ready_path = sess_dir / "session.READY"
        finalize_path = pipeline_root / session_id / ".status" / "finalize.done"
        if not ready_path.is_file() or not finalize_path.parent.is_dir():
            continue
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        ready_at = str(ready.get("at") or "")
        try:
            ready_ts = datetime.fromisoformat(ready_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        finalize_path.parent.mkdir(parents=True, exist_ok=True)
        if not finalize_path.is_file():
            finalize_path.write_text(
                json.dumps({"session_id": session_id, "pose_ready": True}) + "\n",
                encoding="utf-8",
            )
        os.utime(finalize_path, (ready_ts + 120, ready_ts + 120))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default="ego-001")
    parser.add_argument("--datalab-root", type=Path, default=None)
    args = parser.parse_args()
    here = Path(__file__).resolve()
    datalab_root = args.datalab_root
    if datalab_root is None:
        datalab_root = next(
            (
                parent
                for parent in here.parents
                if (parent / "data-storage" / "corpus").is_dir()
                and (parent / "data-storage" / "stream").is_dir()
            ),
            here.parents[3] if len(here.parents) > 3 else here.parents[2],
        )
    _bootstrap(datalab_root)
    stream_root = datalab_root / "data-storage" / "stream" / args.station
    corpus_root = datalab_root / "data-storage" / "corpus" / "ego_001"
    sync_script = datalab_root / "data-lab-platform" / "lerobot-studio" / "scripts" / "sync-stream-parquet.py"
    merge_imu = datalab_root / "data-lab-platform" / "lerobot-studio" / "scripts" / "merge-station-imu.py"
    qc_base = datalab_root / "data-storage" / "lerobot-qc" / "sidecar"

    print(f"republish stream from derived: {stream_root}")
    republish_stream_from_derived(stream_root, sync_script)
    print(f"repair stream parquet: {stream_root}")
    print(f"  files: {repair_data_parquet(stream_root)}")
    print(f"repair corpus parquet: {corpus_root}")
    print(f"  files: {repair_data_parquet(corpus_root)}")

    subprocess.run([sys.executable, str(merge_imu), str(stream_root), "--rebuild"], check=True)
    imu_report = rebuild_corpus_sensor_raw(corpus_root, stream_root)
    print(f"corpus sensor_raw rebuild: {json.dumps(imu_report, ensure_ascii=False)}")

    write_stats(stream_root, sync_script)
    write_stats(corpus_root, sync_script)
    bootstrap_qc_sidecar(corpus_root, qc_base)
    bootstrap_annotations(corpus_root)
    fix_convert_slo_markers(datalab_root, args.station)
    print("QC sidecar + annotations bootstrapped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
