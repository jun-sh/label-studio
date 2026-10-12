"""CLI: undistort camera_head_left for one LeRobot v3 episode using raw/camera_intrinsics.json."""

from __future__ import annotations

import argparse
from pathlib import Path

from ego_capture_studio.offline.undistort import undistort_episode_to_mp4


def main() -> None:
    p = argparse.ArgumentParser(
        description="Undistort observation.images.camera_head_left for one episode (Brown-Conrady via OpenCV)."
    )
    p.add_argument("--dataset-root", type=Path, required=True, help="LeRobot v3 dataset root")
    p.add_argument("--episode-index", type=int, required=True)
    p.add_argument(
        "--video-key",
        type=str,
        default="observation.images.camera_head_left",
        help="LeRobot video feature key",
    )
    p.add_argument("--intrinsics", type=Path, default=None, help="Override camera_intrinsics.json path")
    p.add_argument("--rgb-video", type=Path, default=None, help="Override source mp4 path")
    p.add_argument("--output", type=Path, default=None, help="Output mp4 (default: offline/episode_XX/camera_head_left_undistorted.mp4)")
    p.add_argument(
        "--alpha",
        type=float,
        default=0.0,
        help="OpenCV getOptimalNewCameraMatrix alpha (0=crop, 1=keep all pixels)",
    )
    p.add_argument("--fps", type=float, default=None, help="Override fps for writer / frame skip math")
    args = p.parse_args()

    out = undistort_episode_to_mp4(
        args.dataset_root.resolve(),
        args.episode_index,
        video_key=args.video_key,
        intrinsics_json=args.intrinsics.resolve() if args.intrinsics else None,
        rgb_video=args.rgb_video.resolve() if args.rgb_video else None,
        output_mp4=args.output.resolve() if args.output else None,
        alpha=float(args.alpha),
        fps_override=float(args.fps) if args.fps is not None else None,
    )
    print("Wrote", out)


if __name__ == "__main__":
    main()
