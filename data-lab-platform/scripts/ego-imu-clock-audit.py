#!/usr/bin/env python3
"""Trace IMU/camera clock fields MCAP→materialize→jsonl→parquet; audit clock domains."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DATALAB = ROOT.parent
DERIVE_DIR = ROOT / "lerobot-studio" / "derive"
SYNC_SCRIPT = ROOT / "lerobot-studio" / "scripts" / "sync-stream-parquet.py"

SESSIONS = {
    0: "sess_8aa298f87ba64b07b14afc7dbabab19a",
    1: "sess_de088591b4a94457b49c1a5278292f7d",
    2: "sess_2e8da6fee94a465e9a7fd79f8a6ab59e",
    3: "sess_48bcaf8d17c14fa08041d530b08ce734",
}


def _load_materialize():
    spec = importlib.util.spec_from_file_location("mcap_materialize", DERIVE_DIR / "mcap-materialize.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _mcap_zst_path(station: str, session_id: str) -> Path:
    return DATALAB / "data-storage/stream" / station / "raw/segments" / session_id / "seg_000001.mcap.zst"


def _decompress_mcap(mcap_zst: Path) -> Path:
    out = Path(tempfile.mkdtemp()) / "segment.mcap"
    subprocess.run(["zstd", "-d", "-f", "-o", str(out), str(mcap_zst)], check=True, capture_output=True)
    return out


def _mcap_observation_fields(mcap_path: Path) -> dict:
    from mcap.reader import make_reader

    total = 0
    with_dev = 0
    deltas_ns: list[int] = []
    with open(mcap_path, "rb") as fp:
        reader = make_reader(fp)
        for _s, ch, msg in reader.iter_messages():
            if ch.topic != "/ego/observation/state":
                continue
            total += 1
            pl = json.loads(msg.data)
            dev = pl.get("primary_device_timestamp_ns")
            host = pl.get("timestamp_ns")
            if dev is not None:
                with_dev += 1
            if dev is not None and host is not None:
                deltas_ns.append(int(host) - int(dev))
    return {
        "observation_total": total,
        "primary_device_timestamp_ns_present": with_dev,
        "host_minus_device_ns": {
            "count": len(deltas_ns),
            "min": min(deltas_ns) if deltas_ns else None,
            "max": max(deltas_ns) if deltas_ns else None,
            "mean": (sum(deltas_ns) / len(deltas_ns)) if deltas_ns else None,
        },
    }


def _mcap_imu_vs_device(mcap_path: Path, sample_limit: int = 5000) -> dict:
    from mcap.reader import make_reader

    imu_ts: list[int] = []
    obs_dev: list[int] = []
    with open(mcap_path, "rb") as fp:
        reader = make_reader(fp)
        for _s, ch, msg in reader.iter_messages():
            if ch.topic == "/ego/imu/raw":
                pl = json.loads(msg.data)
                ts = pl.get("ts_ns") or pl.get("timestamp_ns")
                if ts is not None:
                    imu_ts.append(int(ts))
            elif ch.topic == "/ego/observation/state":
                pl = json.loads(msg.data)
                dev = pl.get("primary_device_timestamp_ns")
                if dev is not None:
                    obs_dev.append(int(dev))
            if len(imu_ts) >= sample_limit and len(obs_dev) >= sample_limit:
                break
    imu_ts.sort()
    obs_dev.sort()
    overlap = False
    if imu_ts and obs_dev:
        overlap = not (imu_ts[-1] < obs_dev[0] or obs_dev[-1] < imu_ts[0])
    return {
        "imu_sample_count": len(imu_ts),
        "obs_device_ts_count": len(obs_dev),
        "imu_range_ns": [imu_ts[0], imu_ts[-1]] if imu_ts else None,
        "device_range_ns": [obs_dev[0], obs_dev[-1]] if obs_dev else None,
        "ranges_overlap": overlap,
        "same_unit_assumption": "nanoseconds (mcap_segment_writer)",
    }


def _field_ratio(rows: list[dict], key: str) -> dict:
    if not rows:
        return {"present": 0, "total": 0, "ratio": 0.0}
    present = sum(1 for r in rows if r.get(key) is not None)
    return {"present": present, "total": len(rows), "ratio": present / len(rows)}


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
        if limit and len(rows) >= limit:
            break
    return rows


def _parquet_field_ratio(parquet_path: Path, key: str) -> dict:
    if not parquet_path.is_file():
        return {"present": 0, "total": 0, "ratio": 0.0, "path": None, "column": False}
    schema = pq.read_schema(parquet_path)
    if key not in schema.names:
        return {
            "present": 0,
            "total": pq.read_metadata(parquet_path).num_rows,
            "ratio": 0.0,
            "column": False,
        }
    table = pq.read_table(parquet_path, columns=[key])
    col = table.column(key).to_pylist()
    present = sum(1 for v in col if v is not None)
    return {"present": present, "total": len(col), "ratio": present / len(col) if col else 0.0, "column": True}


def _simulate_sync_parquet(rows: list[dict], work: Path) -> Path:
    meta = work / "meta"
    data = work / "data" / "chunk-000"
    meta.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    info_src = DATALAB / "data-storage/stream/ego-001/meta/info.json"
    info = json.loads(info_src.read_text(encoding="utf-8")) if info_src.is_file() else {"fps": 30, "features": {}}
    (meta / "info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    jsonl = data / "file-000.jsonl"
    jsonl.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    subprocess.run([sys.executable, str(SYNC_SCRIPT), str(work)], check=True, capture_output=True)
    return data / "file-000.parquet"


def audit(station: str = "ego-001") -> dict:
    mod = _load_materialize()
    stream_root = DATALAB / "data-storage/stream" / station
    corpus_root = DATALAB / "data-storage/corpus/ego_001"
    episodes = []

    for ep_idx, session_id in SESSIONS.items():
        mcap_zst = _mcap_zst_path(station, session_id)
        ep_report: dict = {"episode_index": ep_idx, "session_id": session_id, "mcap_archive": str(mcap_zst)}
        if mcap_zst.is_file():
            mcap = _decompress_mcap(mcap_zst)
            ep_report["mcap_observation"] = _mcap_observation_fields(mcap)
            ep_report["mcap_imu_device_overlap"] = _mcap_imu_vs_device(mcap)
            mat_dir = Path(tempfile.mkdtemp())
            mat = mod.materialize_mcap_archive(mcap_zst, mat_dir)
            mat_rows = _read_jsonl(mat_dir / "rows.jsonl")
            ep_report["materialize"] = {
                "frame_count": mat.get("frame_count"),
                "fields": {
                    "primary_device_timestamp_ns": _field_ratio(mat_rows, "primary_device_timestamp_ns"),
                    "timestamp_ns": _field_ratio(mat_rows, "timestamp_ns"),
                },
            }
            if mat_rows:
                sim = Path(tempfile.mkdtemp())
                pq_path = _simulate_sync_parquet(mat_rows, sim)
                ep_report["sync_stream_parquet_sim"] = {
                    "primary_device_timestamp_ns": _parquet_field_ratio(pq_path, "primary_device_timestamp_ns"),
                    "timestamp_ns": _parquet_field_ratio(pq_path, "timestamp_ns"),
                }
        stream_jsonl = stream_root / "data" / "chunk-000" / f"file-{ep_idx:03d}.jsonl"
        stream_rows = _read_jsonl(stream_jsonl, limit=50)
        ep_report["stream_jsonl"] = {
            "path": str(stream_jsonl) if stream_jsonl.is_file() else None,
            "note": "legacy derived jsonl; not authoritative for MCAP field presence",
            "fields": {
                "primary_device_timestamp_ns": _field_ratio(stream_rows, "primary_device_timestamp_ns"),
                "timestamp_ns": _field_ratio(stream_rows, "timestamp_ns"),
            },
        }
        episodes.append(ep_report)

    corpus_pq = corpus_root / "data" / "chunk-000" / "file-000.parquet"
    corpus_imu = corpus_root / "sensor_raw" / "imu" / "chunk-000" / "file-000.parquet"

    mcap_with_field = [
        ep
        for ep in episodes
        if (ep.get("mcap_observation") or {}).get("primary_device_timestamp_ns_present", 0) > 0
    ]
    field_loss = {
        "mcap_has_primary_device_ts": len(mcap_with_field) > 0,
        "mcap_sessions_with_field": [
            {
                "episode_index": ep["episode_index"],
                "session_id": ep["session_id"],
                "present": (ep.get("mcap_observation") or {}).get("primary_device_timestamp_ns_present"),
                "total": (ep.get("mcap_observation") or {}).get("observation_total"),
            }
            for ep in mcap_with_field
        ],
        "materialize_preserves": all(
            (ep.get("materialize") or {}).get("fields", {})
            .get("primary_device_timestamp_ns", {})
            .get("ratio", 0)
            >= 0.99
            for ep in episodes
            if ep.get("materialize")
        ),
        "sync_parquet_preserves_after_fix": all(
            (ep.get("sync_stream_parquet_sim") or {})
            .get("primary_device_timestamp_ns", {})
            .get("ratio", 0)
            >= 0.99
            for ep in episodes
            if ep.get("sync_stream_parquet_sim")
        ),
        "legacy_stream_jsonl_missing": all(
            (ep.get("stream_jsonl") or {}).get("fields", {})
            .get("primary_device_timestamp_ns", {})
            .get("ratio", 0)
            == 0.0
            for ep in episodes
        ),
        "loss_location": "sync-stream-parquet dropped optional meta columns; legacy stream jsonl built before MCAP field path or from parquet without column",
    }

    verification = {
        "imu_align_verified": False,
        "status": "unverified",
        "reason": (
            "primary_device_timestamp_ns present in MCAP and materialize; host-device offset measurable "
            "but IMU-to-camera alignment not proven to commercial tolerance"
        ),
        "gate_action": "fail_if_claiming_imu_align_passed",
        "requires_new_capture": False,
        "requires_rederive": "Re-derive stream/corpus from MCAP with fixed sync-stream-parquet to populate parquet column",
    }

    return {
        "ok": True,
        "station": station,
        "clock_domains": {
            "camera_host": {"field": "timestamp_ns", "unit": "ns", "source": "host/grid at frame commit"},
            "camera_device": {"field": "primary_device_timestamp_ns", "unit": "ns", "source": "OAK device clock"},
            "imu_raw": {"field": "ts_ns", "unit": "ns", "source": "/ego/imu/raw MCAP messages"},
            "imu_parquet": {"field": "imu_timestamp", "unit": "seconds", "source": "sensor_raw parquet after derive"},
        },
        "field_propagation": field_loss,
        "episodes": episodes,
        "corpus_parquet": {
            "data": _parquet_field_ratio(corpus_pq, "primary_device_timestamp_ns"),
            "imu": {
                "path": str(corpus_imu) if corpus_imu.is_file() else None,
                "rows": pq.read_metadata(corpus_imu).num_rows if corpus_imu.is_file() else 0,
            },
        },
        "verification": verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="IMU/camera clock-domain audit with MCAP field trace")
    parser.add_argument("--station", default="ego-001")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.station)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "verification_status": report["verification"]["status"],
                "mcap_has_field": report["field_propagation"]["mcap_has_primary_device_ts"],
                "report": str(args.report),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
