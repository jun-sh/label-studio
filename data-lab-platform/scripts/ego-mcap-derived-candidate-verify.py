#!/usr/bin/env python3
"""Post-build verification for MCAP-derived delivery candidate (isolated).

Evidence tiers (aligned with batch-close-20260911):
  A1 — ffprobe frame count + first-frame PTS per camera (not full-decode audit)
  A2 — ep2/ep3 only: head/mid/tail RGB vs MCAP materialize (12 points/ep)
  A3 — official LeRobot loader: dual backend + head_mid_tail (default candidate gate)
  Full loader (--loader-sample all) is optional release-tier only; not default.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import av
import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DATALAB = ROOT.parent
STUDIO = ROOT / "lerobot-studio"
LEROBOT = DATALAB.parent / "lerobot"
LOADER_GATE = ROOT / "scripts" / "ego-lerobot-loader-gate.py"
IMU_ALIGN_GATE = ROOT / "scripts" / "ego-imu-align-gate.py"

CAMERAS = ("front_left", "front_right", "rear_left", "rear_right")
VIDEO_KEYS = {
    "front_left": "observation.images.camera_front_left",
    "front_right": "observation.images.camera_front_right",
    "rear_left": "observation.images.camera_rear_left",
    "rear_right": "observation.images.camera_rear_right",
}

EPISODES = [
    {"episode_index": 0, "session_id": "sess_8aa298f87ba64b07b14afc7dbabab19a", "device_ts_expected": 0},
    {"episode_index": 1, "session_id": "sess_de088591b4a94457b49c1a5278292f7d", "device_ts_expected": 0},
    {"episode_index": 2, "session_id": "sess_2e8da6fee94a465e9a7fd79f8a6ab59e", "device_ts_expected": "all"},
    {"episode_index": 3, "session_id": "sess_48bcaf8d17c14fa08041d530b08ce734", "device_ts_expected": "all"},
]

# ep2/ep3 sample points match ego-h264-derive-output-verify.py
A2_SAMPLE_BY_EP = {
    2: [0, 87, 175],
    3: [0, 50, 100],
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_materialize():
    spec = importlib.util.spec_from_file_location("mcap_materialize", STUDIO / "derive/mcap-materialize.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _probe_frame_count(mp4: Path) -> int:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=nb_read_frames",
        "-of",
        "default=nokey=1:noprint_wrappers=1",
        str(mp4),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        return int((out.stdout or "0").strip())
    except ValueError:
        return 0


def _ffprobe_pts(mp4: Path, limit: int = 3) -> list[float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp_time,pkt_pts_time",
        "-of",
        "json",
        str(mp4),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return []
    data = json.loads(out.stdout or "{}")
    pts = []
    for fr in data.get("frames") or []:
        t = fr.get("best_effort_timestamp_time")
        if t is None:
            t = fr.get("pkt_pts_time")
        if t is not None:
            pts.append(float(t))
        if len(pts) >= limit:
            break
    return pts


def _decode_mp4_frames(mp4: Path) -> list[np.ndarray]:
    frames = []
    with av.open(str(mp4)) as container:
        for frame in container.decode(video=0):
            frames.append(frame.to_ndarray(format="rgb24"))
    return frames


def _decode_h264_through(h264: Path, max_index: int) -> dict[int, np.ndarray]:
    out: dict[int, np.ndarray] = {}
    with av.open(str(h264), format="h264") as container:
        for i, frame in enumerate(container.decode(video=0)):
            out[i] = frame.to_ndarray(format="rgb24")
            if i >= max_index:
                break
    return out


def _decode_mp4_through(mp4: Path, max_index: int) -> dict[int, np.ndarray]:
    out: dict[int, np.ndarray] = {}
    with av.open(str(mp4)) as container:
        for i, frame in enumerate(container.decode(video=0)):
            out[i] = frame.to_ndarray(format="rgb24")
            if i >= max_index:
                break
    return out


def _frame_mae(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return float("inf")
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def _parquet_field_stats(parquet: Path, field: str) -> dict:
    if not parquet.is_file():
        return {"column": False, "present": 0, "total": 0}
    schema = pq.read_schema(parquet)
    if field not in schema.names:
        return {"column": False, "present": 0, "total": pq.read_metadata(parquet).num_rows}
    col = pq.read_table(parquet, columns=[field]).column(field).to_pylist()
    present = sum(1 for v in col if v is not None)
    total = len(col)
    return {"column": True, "present": present, "total": total, "ratio": present / total if total else 0}


def _video_a1(candidate: Path, episode_index: int, expected_frames: int) -> dict:
    """Frame count + PTS via ffprobe; decode only on ffprobe/count mismatch."""
    checks = []
    for cam in CAMERAS:
        vk = VIDEO_KEYS[cam]
        mp4 = candidate / "videos" / vk / "chunk-000" / f"file-{episode_index:03d}.mp4"
        probe = _probe_frame_count(mp4)
        pts = _ffprobe_pts(mp4, limit=3)
        count_ok = probe == expected_frames
        decode_frames = None
        if not count_ok and mp4.is_file():
            decode_frames = len(_decode_mp4_frames(mp4))
            count_ok = decode_frames == expected_frames == probe
        checks.append(
            {
                "camera": cam,
                "path": str(mp4.relative_to(candidate)),
                "ffprobe_frames": probe,
                "full_decode_frames": decode_frames,
                "expected": expected_frames,
                "pts_first_sec": pts,
                "count_ok": count_ok,
            }
        )
    return {
        "scope": "A1_ffprobe_frame_count_pts",
        "not_claimed": "full_decode_content_audit",
        "ok": all(c["count_ok"] for c in checks),
        "cameras": checks,
    }


def _video_a2(
    candidate: Path,
    episode_index: int,
    session_id: str,
    expected_frames: int,
    work: Path,
) -> dict:
    sample_indices = A2_SAMPLE_BY_EP.get(episode_index) or [
        0,
        expected_frames // 2,
        max(0, expected_frames - 1),
    ]
    mcap_zst = DATALAB / f"data-storage/stream/ego-001/raw/segments/{session_id}/seg_000001.mcap.zst"
    mod = _load_materialize()
    mat_dir = work / f"mat_{session_id}"
    if mat_dir.exists():
        import shutil

        shutil.rmtree(mat_dir)
    mat_dir.mkdir(parents=True)
    mat = mod.materialize_mcap_archive(mcap_zst, mat_dir)
    trim = mat.get("h264_trim") or {}
    align_skip = int(trim.get("trim_content_packet_index") or 0)
    warmup = trim.get("decode_warmup_packets") or {}
    image_checks = []
    max_mp4_idx = max(sample_indices)
    for cam in CAMERAS:
        vk = VIDEO_KEYS[cam]
        mp4 = candidate / "videos" / vk / "chunk-000" / f"file-{episode_index:03d}.mp4"
        mp4_by_idx = _decode_mp4_through(mp4, max_mp4_idx)
        ref_h264 = mat_dir / "streams" / f"{cam}.h264"
        skip = int(warmup.get(cam, 0))
        max_ref_idx = skip + max(sample_indices)
        ref_by_idx = _decode_h264_through(ref_h264, max_ref_idx)
        for idx in sample_indices:
            ref_i = skip + idx
            if idx not in mp4_by_idx or ref_i not in ref_by_idx:
                image_checks.append({"camera": cam, "frame_index": idx, "ok": False, "reason": "index_oob"})
                continue
            mae = _frame_mae(mp4_by_idx[idx], ref_by_idx[ref_i])
            image_checks.append(
                {
                    "camera": cam,
                    "frame_index": idx,
                    "mae_rgb": round(mae, 3),
                    "ok": mae < 8.0,
                    "mcap_source_index": align_skip + idx,
                }
            )
    return {
        "scope": "A2_head_mid_tail_image_vs_mcap_materialize",
        "not_claimed": "full_frame_content_audit",
        "sample_indices": sample_indices,
        "ok": all(c.get("ok") for c in image_checks),
        "checks": image_checks,
    }


def _field_preserve(candidate: Path) -> dict:
    episodes = []
    for ep in EPISODES:
        idx = ep["episode_index"]
        pq_path = candidate / "data" / "chunk-000" / f"file-{idx:03d}.parquet"
        stats = _parquet_field_stats(pq_path, "primary_device_timestamp_ns")
        exp = ep["device_ts_expected"]
        if exp == 0:
            ok = stats.get("present", 0) == 0
        else:
            ok = stats.get("ratio", 0) >= 0.99
        episodes.append(
            {
                "episode_index": idx,
                "session_id": ep["session_id"],
                "primary_device_timestamp_ns": stats,
                "expected": exp,
                "ok": ok,
            }
        )
    return {
        "scope": "MCAP_to_delivery_parquet_field_preservation",
        "imu_align_verified": False,
        "ok": all(e["ok"] for e in episodes),
        "episodes": episodes,
    }


def _run_loader_gate(candidate: Path, report: Path, sample_mode: str) -> dict:
    loader_py = LEROBOT / ".venv/bin/python3"
    if not loader_py.is_file():
        loader_py = Path(sys.executable)
    proc = subprocess.run(
        [
            str(loader_py),
            str(LOADER_GATE),
            str(candidate),
            "--sample",
            sample_mode,
            "--no-dataloader",
            "--lerobot-repo",
            str(LEROBOT),
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
    )
    payload = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
    rep = (payload.get("reports") or [{}])[0]
    tier = "candidate_gate" if sample_mode == "head_mid_tail" else "release_regression"
    return {
        "ok": proc.returncode == 0 and bool(rep.get("ok")),
        "sample_mode": sample_mode,
        "tier": tier,
        "not_claimed": "per_frame_full_loader_audit" if sample_mode == "head_mid_tail" else None,
        "report": str(report.resolve()),
        "failure_count": rep.get("failure_count", 0),
        "sample_count_per_backend": len((rep.get("backends") or {}).get("pyav", {}).get("samples") or []),
        "backends": list((rep.get("backends") or {}).keys()),
    }


def verify(
    candidate: Path,
    work: Path,
    evidence: Path,
    candidate_id: str,
    *,
    loader_sample: str = "head_mid_tail",
) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    manifest_path = candidate / "manifest/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    ep_by_idx = {int(e["episode_index"]): e for e in manifest.get("episodes") or []}

    video_reports = []
    for ep in EPISODES:
        idx = ep["episode_index"]
        expected = int((ep_by_idx.get(idx) or {}).get("frames") or 0)
        a1 = _video_a1(candidate, idx, expected)
        a2 = None
        if idx in (2, 3):
            a2 = _video_a2(candidate, idx, ep["session_id"], expected, work)
        video_reports.append(
            {
                "episode_index": idx,
                "session_id": ep["session_id"],
                "expected_frames": expected,
                "a1_frame_count_pts": a1,
                "a2_head_mid_tail": a2,
                "ok": a1["ok"] and (a2["ok"] if a2 else True),
            }
        )

    field_report = _field_preserve(candidate)
    loader_suffix = "full" if loader_sample == "all" else "smoke"
    loader_report_path = evidence / f"loader-regression-mcap-derived-{loader_suffix}-{candidate_id.split('-')[-1]}.json"
    loader = _run_loader_gate(candidate, loader_report_path, loader_sample)

    imu_audit = {
        "status": "unverified",
        "imu_align_verified": False,
        "reason": "field preservation checked; IMU-to-camera alignment not proven",
        "field_preservation": field_report,
    }
    align_proc = subprocess.run(
        [
            sys.executable,
            str(IMU_ALIGN_GATE),
            "--audit-report",
            str(evidence / "imu-clock-audit-ego-001.json"),
            "--allow-unverified",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    imu_gate = json.loads((align_proc.stdout or "{}").strip() or "{}")
    imu_audit["align_gate"] = imu_gate

    report = {
        "generated_at": _utc_now(),
        "candidate_id": candidate_id,
        "candidate_path": str(candidate.resolve()),
        "evidence_tier": "candidate_gate",
        "loader_gate": loader,
        "video_verification": {
            "episodes": video_reports,
            "ok": all(v["ok"] for v in video_reports),
        },
        "field_preservation": field_report,
        "imu": imu_audit,
        "phases": {
            "video": {
                "status": "recalculated",
                "verification": "passed" if all(v["ok"] for v in video_reports) else "failed",
            },
            "table": {
                "status": "recalculated",
                "verification": "passed" if field_report["ok"] else "failed",
            },
            "imu": {"status": "recalculated", "verification": "unverified"},
            "hands": {"status": "skipped"},
            "depth": {"status": "skipped"},
            "pose": {"status": "skipped"},
        },
        "ok": loader["ok"] and field_report["ok"] and all(v["ok"] for v in video_reports),
    }
    out = evidence / f"mcap-derived-candidate-{candidate_id.split('-')[-1]}.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--candidate-id", default="ego-001-mcap-derived-20260911")
    parser.add_argument(
        "--loader-sample",
        choices=("head_mid_tail", "all"),
        default="head_mid_tail",
        help="candidate gate default; use 'all' only for optional release-tier regression",
    )
    args = parser.parse_args()
    evidence = ROOT / "docs/m0-evidence"
    report = verify(
        args.candidate.resolve(),
        args.work_dir.resolve(),
        evidence,
        args.candidate_id,
        loader_sample=args.loader_sample,
    )
    print(json.dumps({"ok": report["ok"], "candidate": report["candidate_path"]}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
