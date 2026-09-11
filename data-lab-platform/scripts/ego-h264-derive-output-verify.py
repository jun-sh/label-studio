#!/usr/bin/env python3
"""H.264 production derive output verify: unit remux/trim → MP4 → loader image mapping."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import av
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "lerobot-studio"
DERIVE_DIR = STUDIO / "derive"
LEROBOT = ROOT.parent.parent / "lerobot"
LOADER_GATE = ROOT / "scripts" / "ego-lerobot-loader-gate.py"

CAMERAS = ("front_left", "front_right", "rear_left", "rear_right")
CAMERA_TO_VIDEO = {
    "front_left": "observation.images.camera_front_left",
    "front_right": "observation.images.camera_front_right",
    "rear_left": "observation.images.camera_rear_left",
    "rear_right": "observation.images.camera_rear_right",
}

EP2 = {
    "session_id": "sess_2e8da6fee94a465e9a7fd79f8a6ab59e",
    "mcap": "data-storage/stream/ego-001/raw/segments/sess_2e8da6fee94a465e9a7fd79f8a6ab59e/seg_000001.mcap.zst",
    "episode_index": 2,
    "expected_frames": 176,
}
EP3 = {
    "session_id": "sess_48bcaf8d17c14fa08041d530b08ce734",
    "mcap": "data-storage/stream/ego-001/raw/segments/sess_48bcaf8d17c14fa08041d530b08ce734/seg_000001.mcap.zst",
    "episode_index": 3,
}


def _load_materialize():
    spec = importlib.util.spec_from_file_location("mcap_materialize", DERIVE_DIR / "mcap-materialize.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _frame_mae(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return float("inf")
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def _decode_mp4_frames(mp4: Path) -> list[np.ndarray]:
    frames = []
    with av.open(str(mp4)) as container:
        for frame in container.decode(video=0):
            frames.append(frame.to_ndarray(format="rgb24"))
    return frames


def _decode_h264_file(h264: Path) -> list[np.ndarray]:
    frames = []
    with av.open(str(h264), format="h264") as container:
        for frame in container.decode(video=0):
            frames.append(frame.to_ndarray(format="rgb24"))
    return frames


def _ffprobe_pts(mp4: Path, limit: int = 5) -> list[float]:
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


def _setup_station(work: Path, mcap_zst: Path, session_id: str) -> Path:
    station = work / "ego-001"
    seg_dir = station / "raw" / "segments" / session_id
    seg_dir.mkdir(parents=True, exist_ok=True)
    dest = seg_dir / "seg_000001.mcap.zst"
    shutil.copy2(mcap_zst, dest)
    meta_src = ROOT.parent / "data-storage/stream/ego-001/meta/info.json"
    if meta_src.is_file():
        meta_dest = station / "meta"
        meta_dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(meta_src, meta_dest / "info.json")
    (station / "manifest").mkdir(parents=True, exist_ok=True)
    return station


def _run_derive_unit(station: Path, session_id: str) -> dict:
    node = f"""
import {{ deriveUnit }} from './derive/unit.mjs';
const out = await deriveUnit('ego-001', '{station.as_posix()}', '{session_id}');
console.log(JSON.stringify({{ ok: out.ok, frames: out.frames, gate: out.gate, unitDir: out.unitDir }}));
process.exit(out.ok ? 0 : 1);
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node],
        cwd=STUDIO,
        capture_output=True,
        text=True,
    )
    line = ""
    for ln in (proc.stdout or "").splitlines():
        if ln.strip().startswith("{"):
            line = ln.strip()
    if proc.returncode != 0:
        raise RuntimeError(f"deriveUnit failed: {proc.stderr[-800:]}")
    return json.loads(line or "{}")


def _build_loader_dataset(unit_dir: Path, out_root: Path, n_frames: int, fps: float = 30.0) -> Path:
    meta = out_root / "meta"
    data = out_root / "data" / "chunk-000"
    videos = out_root / "videos"
    meta.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    info = {
        "codebase_version": "v3.0",
        "fps": fps,
        "total_frames": n_frames,
        "total_episodes": 1,
        "features": {
            "timestamp": {"dtype": "float32", "shape": [1]},
            "frame_index": {"dtype": "int64", "shape": [1]},
            "episode_index": {"dtype": "int64", "shape": [1]},
            "index": {"dtype": "int64", "shape": [1]},
            "task_index": {"dtype": "int64", "shape": [1]},
            "observation.state": {"dtype": "float32", "shape": [6]},
        },
    }
    for cam, vk in CAMERA_TO_VIDEO.items():
        info["features"][vk] = {
            "dtype": "video",
            "shape": [480, 640, 3],
            "names": ["height", "width", "channels"],
            "info": {"video.fps": fps, "video.codec": "h264", "video.is_depth_map": False},
        }
        src_mp4 = unit_dir / "videos" / f"{vk}.mp4"
        dest = videos / vk / "chunk-000" / "file-000.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_mp4, dest)
    (meta / "info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    rows = []
    for i in range(n_frames):
        rows.append(
            {
                "frame_index": i,
                "episode_index": 0,
                "index": i,
                "task_index": 0,
                "timestamp": i / fps,
                "observation.state": [0.0] * 6,
            }
        )
    import pandas as pd

    pd.DataFrame(rows).to_parquet(data / "file-000.parquet", index=False)
    duration = n_frames / fps if fps > 0 else 0.0
    ep_row = {
        "episode_index": 0,
        "length": n_frames,
        "task_index": 0,
        "dataset_from_index": 0,
        "dataset_to_index": n_frames,
        "chunk_index": 0,
        "file_index": 0,
        "data/chunk_index": 0,
        "data/file_index": 0,
    }
    for vk in CAMERA_TO_VIDEO.values():
        ep_row[f"videos/{vk}/chunk_index"] = 0
        ep_row[f"videos/{vk}/file_index"] = 0
        ep_row[f"videos/{vk}/from_timestamp"] = 0.0
        ep_row[f"videos/{vk}/to_timestamp"] = duration
    ep = pd.DataFrame([ep_row])
    (meta / "episodes" / "chunk-000").mkdir(parents=True, exist_ok=True)
    ep.to_parquet(meta / "episodes" / "chunk-000" / "file-000.parquet", index=False)
    pd.DataFrame([{"task_index": 0, "task": "verify"}]).to_parquet(meta / "tasks.parquet", index=False)
    return out_root


def verify_episode(spec: dict, work: Path, *, sample_indices: list[int]) -> dict:
    mod = _load_materialize()
    datalab = ROOT.parent
    mcap_zst = datalab / spec["mcap"]
    session_id = spec["session_id"]
    station = _setup_station(work, mcap_zst, session_id)

    mat_dir = work / "materialize"
    if mat_dir.exists():
        shutil.rmtree(mat_dir)
    mat_dir.mkdir()
    mat = mod.materialize_mcap_archive(mcap_zst, mat_dir)
    trim = mat.get("h264_trim") or {}
    align_skip = int(trim.get("trim_content_packet_index") or 0)
    warmup = trim.get("decode_warmup_packets") or {}
    expected = int(spec.get("expected_frames") or mat.get("frame_count") or 0)
    if expected <= 0:
        raise RuntimeError(f"invalid expected frame count for {session_id}")

    derive = _run_derive_unit(station, session_id)
    unit_dir = Path(derive["unitDir"])
    mp4_checks = []
    image_checks = []
    for cam in CAMERAS:
        vk = CAMERA_TO_VIDEO[cam]
        mp4 = unit_dir / "videos" / f"{vk}.mp4"
        count = _probe_frame_count(mp4)
        pts = _ffprobe_pts(mp4, limit=3)
        mp4_frames = _decode_mp4_frames(mp4)
        ref_h264 = mat_dir / "streams" / f"{cam}.h264"
        ref_decoded = _decode_h264_file(ref_h264)
        skip = int(warmup.get(cam, 0))
        content_ref = ref_decoded[skip : skip + expected]
        ok_count = count == expected == len(mp4_frames) == len(content_ref)
        mp4_checks.append(
            {
                "camera": cam,
                "mp4_frames": count,
                "decoded_frames": len(mp4_frames),
                "expected": expected,
                "warmup_packets": skip,
                "pts_first_sec": pts,
                "count_ok": ok_count,
            }
        )
        for idx in sample_indices:
            if idx >= expected or idx >= len(mp4_frames) or idx >= len(content_ref):
                image_checks.append({"camera": cam, "frame_index": idx, "ok": False, "reason": "index_oob"})
                continue
            mae = _frame_mae(mp4_frames[idx], content_ref[idx])
            image_checks.append(
                {
                    "camera": cam,
                    "frame_index": idx,
                    "mae_rgb": round(mae, 3),
                    "ok": mae < 8.0,
                    "mcap_source_index": align_skip + idx,
                }
            )

    loader_root = work / "loader_dataset"
    if loader_root.exists():
        shutil.rmtree(loader_root)
    _build_loader_dataset(unit_dir, loader_root, expected)
    loader_py = LEROBOT / ".venv/bin/python3"
    if not loader_py.is_file():
        loader_py = Path(sys.executable)
    lg = subprocess.run(
        [
            str(loader_py),
            str(LOADER_GATE),
            str(loader_root),
            "--sample",
            "head_mid_tail",
            "--lerobot-repo",
            str(LEROBOT),
        ],
        capture_output=True,
        text=True,
    )
    loader_ok = lg.returncode == 0

    all_count_ok = all(c["count_ok"] for c in mp4_checks)
    all_image_ok = all(c.get("ok") for c in image_checks)
    derive_output_ok = bool(derive.get("ok")) and all_count_ok and all_image_ok
    return {
        "ok": derive_output_ok and loader_ok,
        "derive_output_ok": derive_output_ok,
        "session_id": session_id,
        "episode_index": spec.get("episode_index"),
        "materialize": {"frame_count": expected, "align_skip": align_skip, "warmup": warmup},
        "derive": derive,
        "mp4_checks": mp4_checks,
        "image_mapping_checks": image_checks,
        "loader_gate_ok": loader_ok,
        "unit_dir": str(unit_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="H.264 derive output verification")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    ep2 = verify_episode(EP2, args.work_dir / "ep2", sample_indices=[0, 87, 175])
    ep3_work = args.work_dir / "ep3"
    ep3 = verify_episode(
        {**EP3, "expected_frames": None},
        ep3_work,
        sample_indices=[0, 50, 100],
    )
    report = {
        "ok": ep2.get("derive_output_ok") and ep3.get("derive_output_ok") and ep2.get("loader_gate_ok") and ep3.get("loader_gate_ok"),
        "derive_output_ok": ep2.get("derive_output_ok") and ep3.get("derive_output_ok"),
        "ep2": ep2,
        "ep3_targeted": ep3,
        "note": "Verifies production deriveUnit remux+warmup-trim MP4 vs MCAP content frames; loader head/mid/tail on isolated dataset",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(args.report.resolve())}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
