#!/usr/bin/env python3
"""
Official LeRobot-only post-derive validation (MP4 + parquet).

Videos are encoded by LeRobotDataset.save_episode(). This script validates
the on-disk dataset and reports per-camera frame counts. No legacy fallback.

Usage:
  export-videos-from-parquet.py <station_root>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def probe_mp4_frames(path: Path) -> int:
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        return 0
    try:
        return int((res.stdout or "").strip())
    except ValueError:
        return 0


def video_keys_from_info(info: dict) -> list[str]:
    features = info.get("features") or {}
    return [k for k, v in features.items() if isinstance(v, dict) and v.get("dtype") == "video"]


def video_out_path(root: Path, video_key: str) -> Path:
    template = (read_json(root / "meta" / "info.json").get("video_path") or "").strip()
    if not template:
        rel = f"videos/{video_key}/chunk-000/file-000.mp4"
    else:
        rel = template.format(video_key=video_key, chunk_index=0, file_index=0)
    return root / rel


def video_mp4_paths(root: Path, video_key: str) -> list[Path]:
    base = root / "videos" / video_key
    if not base.is_dir():
        return []
    return sorted(base.rglob("file-*.mp4"))


def probe_video_frames(root: Path, video_key: str) -> int:
    paths = video_mp4_paths(root, video_key)
    if not paths:
        legacy = video_out_path(root, video_key)
        return probe_mp4_frames(legacy) if legacy.is_file() else 0
    return sum(probe_mp4_frames(p) for p in paths)


def export_videos(root: Path) -> dict:
    import pyarrow.parquet as pq

    root = root.resolve()
    info = read_json(root / "meta" / "info.json")

    expected = sum(
        pq.read_metadata(p).num_rows for p in sorted(root.glob("data/**/*.parquet"))
    )
    if expected <= 0:
        expected = int(info.get("total_frames") or info.get("ingest_row_count") or 0)
    if expected <= 0:
        raise ValueError("no frames in data parquet shards")

    frames_out: dict[str, int] = {}
    for video_key in video_keys_from_info(info):
        verified = probe_video_frames(root, video_key)
        if verified <= 0:
            raise RuntimeError(f"missing MP4 after LeRobot save_episode: {video_key}")
        frames_out[video_key] = verified
        if verified < expected - 1:
            raise RuntimeError(f"{video_key}: expected>={expected - 1} frames, got {verified}")

    ok = all(expected - 1 <= n <= expected + 1 for n in frames_out.values())
    return {
        "ok": ok,
        "backend": "lerobot",
        "validate": "mp4_probe",
        "expectedFrames": expected,
        "frames": frames_out,
        "fps": float(info.get("fps") or 30),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: export-videos-from-parquet.py <station_root>", file=sys.stderr)
        return 1
    try:
        result = export_videos(Path(sys.argv[1]))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "backend": "lerobot"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
