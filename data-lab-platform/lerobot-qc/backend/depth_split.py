"""Move high-bit-depth depth videos out of the standard LeRobot feature set of a delivery.

Stock LeRobot (0.4.x) has a single ``video_path`` template ending in ``.mp4`` and decodes every
video as 8-bit RGB. Depth streams converted as FFV1/gray16le in Matroska therefore either fail
to load (``.mkv`` path mismatch) or, if aliased, silently lose precision (1 mm -> ~256 mm steps).

The delivery keeps the depth ``.mkv`` files and their per-episode ``videos/<key>/*`` columns in
``meta/episodes``, but lists the depth features in ``meta/depth_features.json`` instead of
``meta/info.json``. Stock LeRobot then loads RGB + state cleanly; depth-aware readers use the
sidecar file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEPTH_SIDECAR = "depth_features.json"
DEPTH_SIDECAR_FORMAT = "qc-depth-sidecar/v1"


def is_depth_feature(feature: dict[str, Any]) -> bool:
    if str(feature.get("dtype") or "") != "video":
        return False
    video_info = feature.get("info") or {}
    return bool(video_info.get("video.is_depth_map")) or "depth.video_path" in video_info


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def split_depth_features(output_root: Path) -> list[str]:
    """Move depth video features from info.json/stats.json into meta/depth_features.json.

    Returns the moved feature keys (empty when the dataset has no depth videos).
    Idempotent: re-running on an already split dataset is a no-op.
    """
    meta = output_root / "meta"
    info = _read_json(meta / "info.json")
    features = info.get("features") or {}
    depth_keys = [key for key, feature in features.items() if is_depth_feature(feature)]
    if not depth_keys:
        return []

    stats_path = meta / "stats.json"
    stats = _read_json(stats_path) if stats_path.exists() else {}
    sidecar_path = meta / DEPTH_SIDECAR
    sidecar = _read_json(sidecar_path) if sidecar_path.exists() else {}
    sidecar_features = dict(sidecar.get("features") or {})
    sidecar_stats = dict(sidecar.get("stats") or {})

    default_path = str(info.get("video_path") or "")
    video_paths: dict[str, str] = dict(sidecar.get("video_paths") or {})
    for key in depth_keys:
        feature = features.pop(key)
        sidecar_features[key] = feature
        video_info = feature.get("info") or {}
        video_paths[key] = str(video_info.get("depth.video_path") or default_path.replace(".mp4", ".mkv"))
        if key in stats:
            sidecar_stats[key] = stats.pop(key)

    _write_json(
        sidecar_path,
        {
            "format": DEPTH_SIDECAR_FORMAT,
            "note": (
                "Depth videos are lossless high-bit-depth streams (e.g. FFV1 gray16le in Matroska). "
                "They are not listed in info.json because stock LeRobot would decode them as 8-bit RGB. "
                "Per-episode locations are in meta/episodes columns videos/<key>/{chunk_index,file_index,"
                "from_timestamp,to_timestamp}; decode with a depth-aware reader to keep metric values."
            ),
            "fps": info.get("fps"),
            "video_paths": video_paths,
            "features": sidecar_features,
            "stats": sidecar_stats,
        },
    )
    info["features"] = features
    _write_json(meta / "info.json", info)
    if stats_path.exists():
        _write_json(stats_path, stats)
    return depth_keys


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        moved = split_depth_features(Path(arg))
        print(f"{arg}: {'moved ' + ', '.join(moved) if moved else 'no depth features'}")
