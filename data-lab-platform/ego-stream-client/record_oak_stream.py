"""Record OAK four-camera + IMU and stream each synced frame to Data Lab (no episode-end batch)."""

from __future__ import annotations

import argparse
import time

from ego_capture_studio.capture.ego_spec import OAK_CAPTURE_FPS, OAK_CAPTURE_IMU_HZ
from ego_capture_studio.capture.frame_jpeg_codec import (
    configure_opencv_threads,
    encode_camera_jpegs,
    log_jpeg_encoder_info,
)
from ego_capture_studio.capture.oak_4p_capture import Oak4pEgoRecorder
from ego_capture_studio.capture.preview_server import start_preview_stack
from ego_capture_studio.capture.stream_upload import FrameStreamUploader


def main() -> None:
    p = argparse.ArgumentParser(description="OAK capture with per-frame stream upload to Data Lab.")
    p.add_argument(
        "--upload-url",
        type=str,
        required=True,
        help="http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/upload",
    )
    p.add_argument(
        "--task",
        type=str,
        default="Perform egocentric manipulation tasks at the laboratory workbench",
        help="Natural-language task description (shown in LeRobot episode list)",
    )
    p.add_argument(
        "--checkpoint-path",
        type=str,
        default="/tmp/ego-stream-checkpoint.json",
        help="Persist sessionId + nextFrameIndex for idempotent resume after disconnect",
    )
    p.add_argument(
        "--episode-seconds",
        type=float,
        default=600.0,
        help="Length of each OAK capture segment before starting the next (continuous stream)",
    )
    p.add_argument("--fps", type=int, default=OAK_CAPTURE_FPS)
    p.add_argument("--imu-hz", type=int, default=OAK_CAPTURE_IMU_HZ)
    imu = p.add_mutually_exclusive_group()
    imu.add_argument("--imu", action="store_true")
    imu.add_argument("--no-imu", action="store_true")
    args = p.parse_args()

    configure_opencv_threads()
    if log_jpeg_encoder_info() != "turbojpeg":
        print("WARNING: PyTurboJPEG unavailable; using OpenCV imencode (higher CPU)", flush=True)

    enable_imu = not args.no_imu
    force_imu = bool(args.imu)

    preview_hub = start_preview_stack()

    uploader = FrameStreamUploader(args.upload_url, checkpoint_path=args.checkpoint_path)
    uploader.set_capture_target_fps(args.fps)
    task = uploader.resume_task() or args.task

    recorder = Oak4pEgoRecorder(
        fps=args.fps,
        imu_hz=args.imu_hz,
        enable_imu=enable_imu,
        force_imu=force_imu,
    )
    recorder.connect()
    uploader.heartbeat(host="10.10.10.214")
    frame_index = 0
    stream_frame_count = 0
    stream_t0 = 0.0
    try:
        probe = recorder.record_episode(3.0)
        if probe.frame_count() == 0:
            raise SystemExit("No frames captured during probe; check OAK device and USB.")
        uploader.start_session(task=task, video_shapes=probe.video_shapes())
        frame_index = uploader.next_frame_index
        if frame_index > 0:
            print(f"resume session={uploader.session_id} next_frame_index={frame_index}")
        uploader.start_upload_worker()
        stream_t0 = time.monotonic()
        print(f"stream started fps_target={args.fps} imu_hz={args.imu_hz} session={uploader.session_id}")

        while True:
            segment_t0 = time.monotonic()
            segment_frames = 0
            for ts_ns, rgb_frames, imu6 in recorder.iter_synced_frames(args.episode_seconds):
                camera_jpegs = encode_camera_jpegs(rgb_frames)
                preview_hub.offer_jpegs(camera_jpegs)
                uploader.enqueue_frame(
                    frame_index=frame_index,
                    timestamp_ns=ts_ns,
                    camera_jpegs=camera_jpegs,
                    imu6=imu6,
                    task=task,
                )
                frame_index += 1
                stream_frame_count += 1
                segment_frames += 1
                if stream_frame_count % 30 == 0:
                    elapsed = max(time.monotonic() - stream_t0, 1e-6)
                    capture_fps = stream_frame_count / elapsed
                    uploader.report_capture_fps(capture_fps)
                    stats = uploader.upload_stats()
                    print(
                        f"captured={frame_index} stream_frames={stream_frame_count} capture_fps={capture_fps:.1f} "
                        f"uploaded={stats['uploaded']} dup={stats['duplicates']} "
                        f"queued={stats['queued']} dropped={stats['dropped']} "
                        f"server_total={stats['serverTotalFrames']} session={stats['sessionId']}"
                    )
            seg_elapsed = max(time.monotonic() - segment_t0, 1e-6)
            print(
                f"segment done frames={segment_frames} elapsed_s={seg_elapsed:.1f} "
                f"next_frame_index={frame_index} session={uploader.session_id}"
            )
    finally:
        uploader.stop_upload_worker()
        uploader.stop_periodic_heartbeat()
        recorder.stop()

    stats = uploader.upload_stats()
    print(
        f"Done. Captured {frame_index} frames, uploaded {stats['uploaded']}, "
        f"dropped {stats['dropped']} to {args.upload_url}"
    )


if __name__ == "__main__":
    main()
