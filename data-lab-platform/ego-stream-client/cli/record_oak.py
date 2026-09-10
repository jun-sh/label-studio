"""Record egocentric four-camera OAK-4P-New + IMU into a local LeRobot v3 dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ego_capture_studio.capture.ego_spec import (
    OAK_CAPTURE_FPS,
    OAK_CAPTURE_IMU_HZ,
    dataset_feature_dict,
)
from ego_capture_studio.capture.lerobot_episode import append_episode_to_dataset
from ego_capture_studio.capture.oak_4p_capture import Oak4pEgoRecorder


def main() -> None:
    p = argparse.ArgumentParser(description="Record OAK-4P-New four-camera ego + IMU into LeRobot v3.")
    p.add_argument(
        "--dataset-root",
        type=Path,
        required=True,
        help="Directory that will contain meta/, data/, videos/, raw/",
    )
    p.add_argument(
        "--repo-id",
        type=str,
        default="local/ego_oak",
        help="Logical repo id stored in meta (local path segment)",
    )
    p.add_argument("--task", type=str, default="demo_task", help="Task string per frame")
    p.add_argument("--episodes", type=int, default=1)
    p.add_argument("--episode-seconds", type=float, default=5.0)
    p.add_argument(
        "--vcodec",
        type=str,
        default="h264",
        help="LeRobot video codec id (e.g. h264, libsvtav1)",
    )
    p.add_argument("--robot-type", type=str, default="oak_4p_ego", help="meta robot_type")
    p.add_argument("--fps", type=int, default=OAK_CAPTURE_FPS, help="Camera FPS")
    p.add_argument("--imu-hz", type=int, default=OAK_CAPTURE_IMU_HZ, help="IMU report rate")
    imu = p.add_mutually_exclusive_group()
    imu.add_argument("--imu", action="store_true", help="Force-enable IMU pipeline")
    imu.add_argument("--no-imu", action="store_true", help="Disable IMU")
    args = p.parse_args()

    root = args.dataset_root.resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists():
        raise SystemExit(
            f"Dataset root {root} already exists. LeRobotDataset.create needs a new directory. "
            "Remove it or pass a different --dataset-root."
        )

    try:
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from lerobot.datasets.video_utils import VideoEncodingManager
    except ImportError as e:
        raise SystemExit(
            "lerobot is required. Install with: uv sync --extra ego_capture"
        ) from e

    enable_imu = not args.no_imu
    force_imu = bool(args.imu)

    recorder = Oak4pEgoRecorder(
        fps=args.fps,
        imu_hz=args.imu_hz,
        enable_imu=enable_imu,
        force_imu=force_imu,
    )

    recorder.connect()
    try:
        probe = recorder.record_episode(3.0)
        if probe.frame_count() == 0:
            raise SystemExit("No frames captured during probe; check OAK device and USB.")
        features = dataset_feature_dict(probe.video_shapes())
    finally:
        recorder.stop()

    dataset = LeRobotDataset.create(
        args.repo_id,
        fps=args.fps,
        root=root,
        robot_type=args.robot_type,
        features=features,
        use_videos=True,
        vcodec=args.vcodec,
        batch_encoding_size=1,
    )

    meta_extras = {
        "capture_device": "OAK-4P-New (DepthAI)",
        "rgb_fps": args.fps,
        "imu_hz": args.imu_hz,
        "camera_layout": "sensexperience_ego_four_cam",
    }

    with VideoEncodingManager(dataset):
        recorder = Oak4pEgoRecorder(
            fps=args.fps,
            imu_hz=args.imu_hz,
            enable_imu=enable_imu,
            force_imu=force_imu,
        )
        recorder.connect()
        try:
            for _ep in range(args.episodes):
                buf = recorder.record_episode(args.episode_seconds)
                append_episode_to_dataset(
                    dataset,
                    buf,
                    task=args.task,
                    dataset_root=root,
                    meta_extras=meta_extras,
                )
        finally:
            recorder.stop()

    print("Dataset written under", root)
    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    print("meta ego_capture:", json.dumps(info.get("ego_capture", {}), indent=2))


if __name__ == "__main__":
    main()
