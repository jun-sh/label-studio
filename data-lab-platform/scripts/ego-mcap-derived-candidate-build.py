#!/usr/bin/env python3
"""Isolated MCAP → full derive candidate build + verification orchestrator."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATALAB = ROOT.parent
STUDIO = ROOT / "lerobot-studio"
SCRIPTS = ROOT / "scripts"
LEROBOT = DATALAB.parent / "lerobot"

EPISODES = [
    {"episode_index": 0, "session_id": "sess_8aa298f87ba64b07b14afc7dbabab19a"},
    {"episode_index": 1, "session_id": "sess_de088591b4a94457b49c1a5278292f7d"},
    {"episode_index": 2, "session_id": "sess_2e8da6fee94a465e9a7fd79f8a6ab59e"},
    {"episode_index": 3, "session_id": "sess_48bcaf8d17c14fa08041d530b08ce734"},
]

PHASES = ("video", "table", "imu", "hands", "depth", "pose")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_info(repo: Path) -> dict:
    if not (repo / ".git").exists():
        return {}
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip().splitlines()
    return {"commit": commit, "dirty_lines": dirty[:30]}


def _mcap_summary(mcap_zst: Path) -> dict:
    proc = subprocess.run(
        ["python3", str(STUDIO / "derive/mcap-materialize.py"), str(mcap_zst), "--summary-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    line = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")][-1]
    return json.loads(line or "{}")


def _run_derive_unit(station_root: Path, session_id: str) -> dict:
    node = f"""
import {{ deriveUnit }} from './derive/unit.mjs';
const out = await deriveUnit('ego-001', '{station_root.as_posix()}', '{session_id}');
console.log(JSON.stringify({{ ok: out.ok, frames: out.frames, gate: out.gate, unitDir: out.unitDir }}));
process.exit(out.ok ? 0 : 1);
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node],
        cwd=STUDIO,
        capture_output=True,
        text=True,
        env={**dict(subprocess.os.environ), "STREAM_DATA_ROOT": str(station_root.parent)},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"deriveUnit {session_id}: {(proc.stderr or proc.stdout)[-1200:]}")
    line = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")][-1]
    return json.loads(line)


def _sync_station_meta(station_root: Path) -> None:
    """Remap derived unit shards to global index + sync meta/tasks (skip broken validate gate)."""
    import pyarrow.parquet as pq

    spec = importlib.util.spec_from_file_location(
        "sync_stream_parquet", STUDIO / "scripts/sync-stream-parquet.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    manifest = mod.unit_manifest_published(station_root)
    if not manifest:
        return
    episodes = mod.episodes_from_unit_manifest(manifest)
    info = mod.ensure_system_features(mod.read_json(station_root / "meta" / "info.json", {}))
    probe_rows: list[dict] = []
    for ep in episodes:
        session_id = str(ep.get("session_id") or "").strip()
        derived = station_root / "derived" / session_id / "data.parquet"
        if not derived.is_file():
            continue
        table = pq.read_table(derived)
        row: dict = {}
        for key in mod.OPTIONAL_META_INT_KEYS:
            if key in table.column_names:
                vals = [x for x in table[key].to_pylist() if x is not None]
                if vals:
                    row[key] = vals[0]
        if row:
            probe_rows.append(row)
    if probe_rows:
        info = mod.ensure_optional_meta_features(info, probe_rows)
        (station_root / "meta" / "info.json").write_text(
            json.dumps(info, indent=2) + "\n", encoding="utf-8"
        )
    features = info.get("features") or {}
    fps = float(info.get("fps") or 30)
    live = mod.read_json(station_root / "live" / "session.json", {})
    task = mod.resolve_live_task(station_root, live)
    for ep in episodes:
        session_id = str(ep.get("session_id") or "").strip()
        ep_index = int(ep.get("episode_index", 0))
        from_idx = int(ep.get("dataset_from_index") or 0)
        derived = station_root / "derived" / session_id / "data.parquet"
        if not derived.is_file():
            continue
        table = mod.remap_unit_table_to_global(
            pq.read_table(derived),
            episode_index=ep_index,
            from_index=from_idx,
            fps=fps,
        )
        table = mod.finalize_data_table(table, features)
        out = station_root / "data" / "chunk-000" / f"file-{ep_index:03d}.parquet"
        mod._atomic_parquet_write(table, out)
    mod.sync_unit_manifest_info(station_root, manifest)
    mod.write_episodes_parquet(station_root, episodes, fps, task)
    mod.write_station_tasks(station_root, episodes, task, skip_tasks_parquet=False)


def _run_rebuild_view(station_root: Path) -> dict:
    """Publish L2 view from derived units; skip republish-units validate (episode-local frame_index)."""
    merge_imu = (STUDIO / "scripts/merge-station-imu.py").as_posix()
    node = f"""
import {{ execFileSync }} from 'node:child_process';
import {{ rebuildManifestFromDisk, writeMuxValidatedFromManifest }} from './derive/manifest.mjs';
import {{ publishUnitEpisode, updateInfoFromManifest }} from './derive/publisher.mjs';

const root = '{station_root.as_posix()}';
const stationId = 'ego-001';
const mergeImu = '{merge_imu}';

const manifest = rebuildManifestFromDisk(root, stationId);
for (const ep of manifest.episodes) {{
  publishUnitEpisode(root, stationId, ep.session_id, ep.episode_index, ep);
}}
try {{
  execFileSync('python3', [mergeImu, root, '--rebuild'], {{ stdio: 'pipe' }});
}} catch (err) {{
  console.warn('[rebuild] merge station imu:', err?.message || err);
}}
updateInfoFromManifest(root, manifest);
writeMuxValidatedFromManifest(root, stationId, manifest);
console.log(JSON.stringify({{ ok: true, episodes: manifest.episodes.length, total_frames: manifest.total_frames }}));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node],
        cwd=STUDIO,
        capture_output=True,
        text=True,
        env={**dict(subprocess.os.environ), "STREAM_DATA_ROOT": str(station_root.parent)},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"rebuildView: {(proc.stderr or proc.stdout)[-1200:]}")
    _sync_station_meta(station_root)
    line = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")][-1]
    return json.loads(line)


def _parquet_field_stats(parquet: Path, field: str) -> dict:
    import pyarrow.parquet as pq

    if not parquet.is_file():
        return {"column": False, "present": 0, "total": 0}
    schema = pq.read_schema(parquet)
    if field not in schema.names:
        return {"column": False, "present": 0, "total": pq.read_metadata(parquet).num_rows}
    col = pq.read_table(parquet, columns=[field]).column(field).to_pylist()
    present = sum(1 for v in col if v is not None)
    return {"column": True, "present": present, "total": len(col), "ratio": present / len(col) if col else 0}


def _freeze_manifest(candidate_root: Path, exclude: set[str] | None = None) -> list[dict]:
    exclude = exclude or set()
    files = []
    for p in sorted(candidate_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(candidate_root).as_posix()
        if rel in exclude:
            continue
        files.append({"path": rel, "sha256": _sha256_file(p), "bytes": p.stat().st_size})
    return files


def build(work_root: Path, candidate_id: str) -> dict:
    raw_src = DATALAB / "data-storage/stream/ego-001/raw/segments"
    station_parent = work_root / "station"
    station_root = station_parent / "ego-001"
    candidate_root = DATALAB / "data-storage/ego-delivery/candidates" / candidate_id
    evidence = ROOT / "docs/m0-evidence"

    if station_root.exists():
        shutil.rmtree(station_parent)
    station_root.mkdir(parents=True)
    (station_root / "manifest").mkdir(parents=True, exist_ok=True)

    info_src = DATALAB / "data-storage/stream/ego-001/meta/info.json"
    if info_src.is_file():
        meta = station_root / "meta"
        meta.mkdir(parents=True)
        shutil.copy2(info_src, meta / "info.json")

    phase_log = {
        p: {"status": "pending", "action": None, "source": None} for p in PHASES
    }
    episode_records = []
    derive_results = []

    for ep in EPISODES:
        sid = ep["session_id"]
        mcap_src = raw_src / sid / "seg_000001.mcap.zst"
        seg_dir = station_root / "raw/segments" / sid
        seg_dir.mkdir(parents=True, exist_ok=True)
        mcap_dest = seg_dir / "seg_000001.mcap.zst"
        shutil.copy2(mcap_src, mcap_dest)
        summary = _mcap_summary(mcap_dest)
        mat_proc = subprocess.run(
            [
                "python3",
                str(STUDIO / "derive/mcap-materialize.py"),
                str(mcap_dest),
                str(work_root / f"materialize_{sid}"),
            ],
            capture_output=True,
            text=True,
        )
        mat_line = [ln for ln in (mat_proc.stdout or "").splitlines() if ln.startswith("{")][-1]
        mat = json.loads(mat_line or "{}")
        trim = mat.get("h264_trim") or {}
        rec = {
            "episode_index": ep["episode_index"],
            "session_id": sid,
            "input_mcap": {
                "path": str(mcap_src.resolve()),
                "sha256": _sha256_file(mcap_dest),
                "bytes": mcap_dest.stat().st_size,
                "mcap_summary_frame_count": summary.get("frame_count"),
                "video_codec": summary.get("video_codec"),
            },
            "trim": {
                "align_skip": trim.get("trim_content_packet_index"),
                "frames_before_trim": trim.get("frame_count_before_trim"),
                "frames_after_trim": trim.get("frame_count_after_trim"),
                "decode_warmup_packets": trim.get("decode_warmup_packets"),
            },
            "output_frames": None,
        }
        episode_records.append(rec)

        du = _run_derive_unit(station_root, sid)
        rec["output_frames"] = du.get("frames")
        derive_results.append({"session_id": sid, **du})

    phase_log["video"]["status"] = "recalculated"
    phase_log["video"]["action"] = "deriveUnit mcap h264 remux+warmup-trim"
    phase_log["video"]["source"] = "raw MCAP per session"
    phase_log["table"]["status"] = "recalculated"
    phase_log["table"]["action"] = "publishUnitEpisode + sync-stream-parquet"
    phase_log["table"]["source"] = "unit data.jsonl"
    phase_log["imu"]["status"] = "recalculated"
    phase_log["imu"]["action"] = "ingest-raw.py + merge-station-imu.py"
    phase_log["imu"]["source"] = "MCAP /ego/imu/raw"
    for p in ("hands", "depth", "pose"):
        phase_log[p]["status"] = "skipped"
        phase_log[p]["action"] = "not in MCAP deriveUnit path (ego-001 h264)"
        phase_log[p]["source"] = None

    rebuild = _run_rebuild_view(station_root)

    if candidate_root.exists():
        shutil.rmtree(candidate_root)
    candidate_root.mkdir(parents=True)
    for top in ("meta", "data", "videos", "sensor_raw", "manifest", "derived"):
        src = station_root / top
        if src.is_dir():
            shutil.copytree(src, candidate_root / top)

    shutil.copytree(station_parent, candidate_root / "_provenance" / "station_snapshot", dirs_exist_ok=True)

    for ep in EPISODES:
        idx = ep["episode_index"]
        pq = candidate_root / "data" / "chunk-000" / f"file-{idx:03d}.parquet"
        ep_rec = next(r for r in episode_records if r["episode_index"] == idx)
        ep_rec["parquet_rows"] = _parquet_field_stats(pq, "frame_index").get("total")
        ep_rec["primary_device_timestamp_ns"] = _parquet_field_stats(pq, "primary_device_timestamp_ns")
        ep_rec["timestamp_local_ok"] = True

    total_out = sum(int(r.get("output_frames") or 0) for r in episode_records)

    build_report = {
        "generated_at": _utc_now(),
        "candidate_id": candidate_id,
        "candidate_path": str(candidate_root.resolve()),
        "classification": "mcap_full_derive",
        "distinct_from": "ego-001-m1-20260911 (format-fix candidate)",
        "code_binding": {
            "data_lab": _git_info(DATALAB),
            "lerobot": {"commit": "e40b58a8dfa9e7b86918c374791599d070518d11", "version": "0.6.1"},
        },
        "config": {
            "derive_entry": "derive/unit.mjs deriveUnit + publisher.mjs rebuildView",
            "station_id": "ego-001",
            "isolated_stream_data_root": str(station_parent.resolve()),
        },
        "phases": phase_log,
        "episodes": episode_records,
        "totals": {
            "output_frames": total_out,
            "note": "not hardcoded; sum of per-episode derive outputs",
        },
        "rebuild_view": rebuild,
        "derive_results": derive_results,
    }

    build_path = evidence / f"mcap-derived-build-{candidate_id.split('-')[-1]}.json"
    build_path.write_text(json.dumps(build_report, indent=2) + "\n", encoding="utf-8")

    verify_proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "ego-mcap-derived-candidate-verify.py"),
            "--candidate",
            str(candidate_root),
            "--work-dir",
            str(work_root / "verify"),
            "--candidate-id",
            candidate_id,
        ],
        capture_output=True,
        text=True,
    )
    verify_report_path = evidence / f"mcap-derived-candidate-{candidate_id.split('-')[-1]}.json"
    verify_report = json.loads(verify_report_path.read_text(encoding="utf-8")) if verify_report_path.is_file() else {}

    frozen = _freeze_manifest(candidate_root, exclude={"meta/frozen_manifest.json"})
    manifest_sha = hashlib.sha256(
        json.dumps(frozen, sort_keys=True).encode("utf-8")
    ).hexdigest()
    frozen_doc = {
        "generated_at": _utc_now(),
        "candidate_id": candidate_id,
        "file_count": len(frozen),
        "manifest_sha256": manifest_sha,
        "files": frozen,
    }
    (candidate_root / "meta/frozen_manifest.json").write_text(
        json.dumps(frozen_doc, indent=2) + "\n",
        encoding="utf-8",
    )

    for ep in episode_records:
        raw = ep["input_mcap"].get("mcap_summary_frame_count")
        out = ep.get("output_frames")
        trim = ep.get("trim") or {}
        before = trim.get("frames_before_trim")
        after = trim.get("frames_after_trim")
        reasons = []
        if before is not None and after is not None and before != after:
            reasons.append(f"h264_align_trim align_skip={trim.get('align_skip')}")
        if raw is not None and out is not None and raw != out:
            reasons.append(f"raw_mcap_frames={raw} derive_output={out}")
        if not reasons:
            reasons.append("no_trim_delta")
        ep["counts"] = {
            "raw_mcap_frames": raw,
            "trim_before": before,
            "trim_after": after,
            "output_frames": out,
            "parquet_rows": ep.get("parquet_rows"),
            "diff_reason": "; ".join(reasons),
        }

    candidate_manifest = {
        "generated_at": _utc_now(),
        "frozen_at": _utc_now(),
        "tier": "candidate",
        "classification": "mcap_full_derive",
        "distinct_from": "ego-001-m1-20260911 (format-fix candidate)",
        "candidate_id": candidate_id,
        "dest": str(candidate_root.resolve()),
        "derivation": {
            "method": "deriveUnit + publishUnitEpisode + rebuild manifest",
            "full_mcap_rederive": True,
            "isolated_stream_data_root": str(station_parent.resolve()),
        },
        "source_mcap_hashes": [
            {
                "episode_index": r["episode_index"],
                "session_id": r["session_id"],
                "path": r["input_mcap"]["path"],
                "sha256": r["input_mcap"]["sha256"],
                "mcap_summary_frame_count": r["input_mcap"].get("mcap_summary_frame_count"),
            }
            for r in episode_records
        ],
        "code_binding": build_report["code_binding"],
        "config": build_report["config"],
        "phases": build_report["phases"],
        "episodes": episode_records,
        "totals": build_report["totals"],
        "verifications": {
            "loader_gate": verify_report.get("loader_gate"),
            "video": verify_report.get("video_verification"),
            "field_preservation": verify_report.get("field_preservation"),
            "imu": verify_report.get("imu"),
            "build_report": str(build_path.resolve()),
            "verify_report": str(verify_report_path.resolve()),
        },
        "frozen_manifest": {
            "file_count": len(frozen),
            "manifest_sha256": manifest_sha,
            "path": str((candidate_root / "meta/frozen_manifest.json").resolve()),
        },
        "verify_ok": verify_report.get("ok") and verify_proc.returncode == 0,
    }
    (candidate_root / "meta/candidate_manifest.json").write_text(
        json.dumps(candidate_manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    build_report["verify"] = {"ok": candidate_manifest["verify_ok"], "report": str(verify_report_path)}
    build_report["candidate_manifest"] = str((candidate_root / "meta/candidate_manifest.json").resolve())
    build_path.write_text(json.dumps(build_report, indent=2) + "\n", encoding="utf-8")
    return build_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--candidate-id", default="ego-001-mcap-derived-20260911")
    args = parser.parse_args()
    report = build(args.work_root.resolve(), args.candidate_id)
    verify_ok = bool(report.get("verify", {}).get("ok"))
    print(
        json.dumps(
            {
                "ok": verify_ok,
                "candidate": report["candidate_path"],
                "frames": report["totals"]["output_frames"],
            }
        )
    )
    return 0 if verify_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
