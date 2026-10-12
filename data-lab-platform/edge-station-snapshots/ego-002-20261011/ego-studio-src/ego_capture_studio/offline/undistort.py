"""Undistort ego RGB using Brown-Conrady intrinsics saved at capture time."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from ego_capture_studio.offline.lerobot_episode_meta import (
    find_episode_metadata_row,
    load_dataset_info,
    resolve_episode_video_path,
)


def resolve_intrinsics_for_video(
    data: dict[str, Any], video_key: str
) -> dict[str, Any]:
    cameras = data.get("cameras")
    if isinstance(cameras, dict) and video_key in cameras:
        return cameras[video_key]
    if isinstance(cameras, dict) and cameras:
        return next(iter(cameras.values()))
    return data


def load_camera_intrinsics_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def intrinsics_to_opencv(
    data: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    w, h = int(data["width"]), int(data["height"])
    k = np.array(
        [
            [float(data["fx"]), 0.0, float(data["ppx"])],
            [0.0, float(data["fy"]), float(data["ppy"])],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    coeffs = data.get("coeffs") or [0.0] * 5
    d = np.array(list(coeffs)[:5], dtype=np.float64).reshape(5, 1)
    return k, d, (w, h)


def build_undistort_maps(
    k: np.ndarray,
    d: np.ndarray,
    size: tuple[int, int],
    *,
    alpha: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    try:
        import cv2
    except ImportError as e:
        raise RuntimeError("opencv-python-headless is required for undistort") from e

    new_k, roi = cv2.getOptimalNewCameraMatrix(k, d, size, alpha, size)
    map1, map2 = cv2.initUndistortRectifyMap(k, d, None, new_k, size, cv2.CV_16SC2)
    return map1, map2, new_k, roi


def remap_bgr(bgr: np.ndarray, map1: np.ndarray, map2: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.remap(bgr, map1, map2, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def iterate_episode_bgr_frames(
    rgb_video: Path,
    *,
    fps: float,
    from_timestamp_s: float,
    num_frames: int,
) -> Iterator[np.ndarray]:
    """Sequential decode: skip frames before episode segment, then yield num_frames BGR frames."""
    import cv2

    cap = cv2.VideoCapture(str(rgb_video))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {rgb_video}")

    cap_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if cap_fps <= 1e-6:
        cap_fps = float(fps)
    start_frame = int(round(float(from_timestamp_s) * cap_fps))
    idx = 0
    while idx < start_frame:
        ok, _ = cap.read()
        if not ok:
            cap.release()
            raise RuntimeError(f"Video shorter than start_frame={start_frame} (from_timestamp_s={from_timestamp_s})")
        idx += 1

    for _ in range(num_frames):
        ok, bgr = cap.read()
        if not ok:
            cap.release()
            raise RuntimeError("Unexpected end of video while reading episode frames")
        yield bgr
    cap.release()


def default_undistorted_video_path(dataset_root: Path, episode_index: int) -> Path:
    return (
        dataset_root / "offline" / f"episode_{episode_index:06d}" / "camera_head_left_undistorted.mp4"
    ).resolve()


def undistort_episode_to_mp4(
    dataset_root: Path,
    episode_index: int,
    *,
    video_key: str = "observation.images.camera_head_left",
    intrinsics_json: Path | None = None,
    rgb_video: Path | None = None,
    output_mp4: Path | None = None,
    alpha: float = 0.0,
    fps_override: float | None = None,
) -> Path:
    """
    Read distorted RGB from the LeRobot episode mp4, undistort with raw/camera_intrinsics.json,
    write offline/episode_XXXXXX/camera_head_left_undistorted.mp4 (default output path).
    """
    import cv2

    root = dataset_root.resolve()
    info = load_dataset_info(root)
    row = find_episode_metadata_row(root, episode_index)
    fps = float(fps_override if fps_override is not None else info.get("fps") or 30)

    intr_path = intrinsics_json or (root / "raw" / f"episode_{episode_index:06d}" / "camera_intrinsics.json")
    intr_data = resolve_intrinsics_for_video(
        load_camera_intrinsics_json(intr_path), video_key
    )
    k, d, intr_size = intrinsics_to_opencv(intr_data)
    map1, map2, new_k, roi = build_undistort_maps(k, d, intr_size, alpha=alpha)

    vid_path = rgb_video or resolve_episode_video_path(root, info, row, video_key)
    if not vid_path.is_file():
        raise FileNotFoundError(vid_path)

    from_ts = float(row[f"videos/{video_key}/from_timestamp"])
    n_frames = int(row["length"])

    out_path = output_mp4 or default_undistorted_video_path(root, episode_index)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    writer: cv2.VideoWriter | None = None
    try:
        for bgr in iterate_episode_bgr_frames(vid_path, fps=fps, from_timestamp_s=from_ts, num_frames=n_frames):
            if bgr.shape[1] != intr_size[0] or bgr.shape[0] != intr_size[1]:
                raise ValueError(
                    f"Video frame size {bgr.shape[1]}x{bgr.shape[0]} != intrinsics {intr_size[0]}x{intr_size[1]}"
                )
            und = remap_bgr(bgr, map1, map2)
            if writer is None:
                h, w = und.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
                if not writer.isOpened():
                    raise RuntimeError(f"Failed to open VideoWriter for {out_path}")
            writer.write(und)
    finally:
        if writer is not None:
            writer.release()

    meta_sidecar = out_path.with_suffix(".meta.json")

    def _rel_under_root(p: Path) -> str:
        try:
            return str(p.relative_to(root))
        except ValueError:
            return str(p)

    meta_sidecar.write_text(
        json.dumps(
            {
                "episode_index": episode_index,
                "video_key": video_key,
                "source_rgb_video": _rel_under_root(vid_path),
                "camera_intrinsics": _rel_under_root(intr_path),
                "from_timestamp_s": from_ts,
                "num_frames": n_frames,
                "fps": fps,
                "new_camera_matrix": new_k.tolist(),
                "undistort_roi": list(map(int, roi)),
                "distortion_model_name": intr_data.get("distortion_model_name"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return out_path
