"""OAK capture: local segment store + live preview only (upload via upload_segments CLI)."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import deque
from pathlib import Path

from ego_capture_studio.capture.ego_spec import OAK_CAPTURE_FPS, OAK_CAPTURE_IMU_HZ
from ego_capture_studio.capture.frame_jpeg_codec import (
    configure_opencv_threads,
    encode_camera_bgr_jpegs,
    log_jpeg_encoder_info,
)
from ego_capture_studio.capture.oak_4p_capture import Oak4pEgoRecorder
from ego_capture_studio.capture.preview_server import start_preview_stack
from ego_capture_studio.capture.segment_store import SegmentCaptureWriter, new_session_id

try:
    from ego_capture_studio.capture.stream_upload import FrameStreamUploader
except ImportError:
    FrameStreamUploader = None  # type: ignore[misc, assignment]


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def _load_checkpoint_session(checkpoint_path: Path) -> tuple[str | None, str | None]:
    if not checkpoint_path.is_file():
        return None, None
    try:
        raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    sid = raw.get("sessionId")
    task = raw.get("task")
    sid_out = sid.strip() if isinstance(sid, str) and sid.strip() else None
    return (sid_out, task if isinstance(task, str) else None)


def _strict_emit_path(checkpoint_path: Path) -> Path:
    return checkpoint_path.parent / "strict_emit_ts.json"


def _load_strict_emit_ts_ns(checkpoint_path: Path) -> int | None:
    path = _strict_emit_path(checkpoint_path)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return int(raw["strictLastEmitTsNs"])
    except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None


def _persist_strict_emit_ts_ns(checkpoint_path: Path, ts_ns: int) -> None:
    if ts_ns <= 0:
        return
    path = _strict_emit_path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"strictLastEmitTsNs": int(ts_ns)}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _append_visual_frame(
    writer: SegmentCaptureWriter,
    preview_hub,
    recorder: Oak4pEgoRecorder,
    *,
    timestamp_ns: int,
    capture_out: dict,
    preview_out: dict,
    imu6,
    camera_ts_offset_ns: dict[str, int] | None = None,
) -> None:
    if recorder.use_hw_h264:
        if preview_out:
            preview_hub.offer_jpegs(preview_out)
        writer.append_frame(
            timestamp_ns=timestamp_ns,
            camera_jpegs=capture_out,
            imu6=imu6,
            camera_ts_offset_ns=camera_ts_offset_ns,
        )
    elif recorder.use_hw_jpeg:
        preview_hub.offer_jpegs(capture_out)
        if preview_out:
            preview_hub.offer_jpegs(preview_out)
        writer.append_frame(
            timestamp_ns=timestamp_ns,
            camera_jpegs=capture_out,
            imu6=imu6,
            camera_ts_offset_ns=camera_ts_offset_ns,
        )
    else:
        camera_jpegs = encode_camera_bgr_jpegs(capture_out)
        preview_hub.offer_jpegs(camera_jpegs)
        if preview_out:
            preview_hub.offer_jpegs(encode_camera_bgr_jpegs(preview_out))
        writer.append_frame(
            timestamp_ns=timestamp_ns,
            camera_jpegs=camera_jpegs,
            imu6=imu6,
            camera_ts_offset_ns=camera_ts_offset_ns,
        )


def main() -> None:
    p = argparse.ArgumentParser(
        description="OAK edge capture (Scheme A): segments on disk + MJPEG preview, no live upload.",
    )
    p.add_argument(
        "--heartbeat-url",
        type=str,
        default=os.environ.get(
            "DATALAB_HEARTBEAT_URL",
            "http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/upload",
        ),
        help="Optional ingest URL for heartbeat only (station online in collection UI)",
    )
    p.add_argument(
        "--task",
        type=str,
        default="",
    )
    p.add_argument(
        "--checkpoint-path",
        type=str,
        default=os.environ.get(
            "EGO_CAPTURE_CHECKPOINT",
            "/home/server/cache/ego-lan-214/segments/checkpoint.json",
        ),
    )
    p.add_argument(
        "--segment-root",
        type=str,
        default=os.environ.get("EGO_SEGMENT_ROOT", "/home/server/cache/ego-lan-214/segments"),
    )
    p.add_argument("--episode-seconds", type=float, default=600.0)
    p.add_argument("--fps", type=int, default=OAK_CAPTURE_FPS)
    p.add_argument("--imu-hz", type=int, default=OAK_CAPTURE_IMU_HZ)
    p.add_argument("--no-heartbeat", action="store_true")
    imu = p.add_mutually_exclusive_group()
    imu.add_argument("--imu", action="store_true")
    imu.add_argument("--no-imu", action="store_true")
    args = p.parse_args()

    configure_opencv_threads()
    if log_jpeg_encoder_info() != "turbojpeg":
        print("WARNING: PyTurboJPEG unavailable; using OpenCV imencode (higher CPU)", flush=True)

    enable_imu = not args.no_imu
    force_imu = bool(args.imu)
    checkpoint_path = Path(args.checkpoint_path)
    strict_20hz = _env_flag("EGO_STRICT_20HZ")
    interval_ms = int(os.environ.get("EGO_FRAME_INTERVAL_MS", "50"))
    imu_interpolate = _env_flag("EGO_IMU_INTERPOLATE", "1")

    preview_hub = start_preview_stack()

    ck_session, ck_task = _load_checkpoint_session(checkpoint_path)
    session_id = ck_session or os.environ.get("EGO_CAPTURE_SESSION_ID") or new_session_id()
    task = ck_task or args.task
    os.environ.setdefault("EGO_SEGMENT_ROOT", args.segment_root)
    os.environ["EGO_CAPTURE_SESSION_ID"] = session_id

    writer = SegmentCaptureWriter.from_env(task=task, checkpoint_path=checkpoint_path)

    capture_no_heartbeat = args.no_heartbeat or _env_flag("EGO_CAPTURE_NO_HEARTBEAT")
    heartbeat: FrameStreamUploader | None = None
    if (
        not capture_no_heartbeat
        and args.heartbeat_url
        and FrameStreamUploader is not None
    ):
        heartbeat = FrameStreamUploader(args.heartbeat_url, checkpoint_path=checkpoint_path)
        heartbeat.session_id = session_id

    device_fps = int(os.environ.get("OAK_DEVICE_FPS", "30"))
    storage_h264 = os.environ.get("SEGMENT_H264", "0").strip().lower() in ("1", "true", "yes")
    recorder = Oak4pEgoRecorder(
        fps=args.fps,
        device_fps=device_fps,
        imu_hz=args.imu_hz,
        enable_imu=enable_imu,
        force_imu=force_imu,
    )
    recorder.connect()

    if strict_20hz:
        resumed_emit = _load_strict_emit_ts_ns(checkpoint_path)
        if resumed_emit is not None:
            recorder._strict_last_emit_ts_ns = int(resumed_emit)
            print(f"strict_emit_resume ts_ns={resumed_emit}", flush=True)

    if heartbeat is not None:
        try:
            heartbeat.heartbeat(host=os.environ.get("DATALAB_CAPTURE_HOST", "10.10.10.214"))
        except Exception as exc:
            print(f"heartbeat warning: {exc}", flush=True)

    frame_count = 0
    t0 = time.monotonic()
    wall_emit_times: deque[float] = deque(maxlen=120)
    try:
        probe = recorder.record_episode(3.0)
        if probe.frame_count() == 0:
            raise SystemExit("No frames captured during probe; check OAK device and USB.")
        print(
            f"capture-only session={session_id} segment_root={args.segment_root} "
            f"fps_target={args.fps} device_fps={device_fps} imu_hz={args.imu_hz} "
            f"hw_jpeg={recorder.use_hw_jpeg} hw_h264={recorder.use_hw_h264} "
            f"storage_h264={int(storage_h264)} strict_20hz={int(strict_20hz)} "
            f"interval_ms={interval_ms} imu_interpolate={int(imu_interpolate)}",
            flush=True,
        )

        if strict_20hz:
            # Do not restart iter_strict_20hz_frames on a short episode window: each restart
            # resets grid state and (without persisted emit) causes multi-second timestamp gaps.
            strict_duration_s = float(
                os.environ.get("EGO_STRICT_EPISODE_SECONDS", "86400")
            )
            frame_iter = recorder.iter_strict_20hz_frames(
                strict_duration_s,
                interval_ms=interval_ms,
                imu_interpolate=imu_interpolate,
            )
            for ts_ns, capture_out, preview_out, imu6, cam_offsets in frame_iter:
                emit_mono = time.monotonic()
                _append_visual_frame(
                    writer,
                    preview_hub,
                    recorder,
                    timestamp_ns=ts_ns,
                    capture_out=capture_out,
                    preview_out=preview_out,
                    imu6=imu6,
                    camera_ts_offset_ns=cam_offsets,
                )
                frame_count += 1
                wall_emit_times.append(emit_mono)
                if frame_count % 100 == 0 and recorder._strict_last_emit_ts_ns is not None:
                    _persist_strict_emit_ts_ns(
                        checkpoint_path, int(recorder._strict_last_emit_ts_ns)
                    )
                if frame_count % 20 == 0:
                    if len(wall_emit_times) >= 2:
                        wall_span = wall_emit_times[-1] - wall_emit_times[0]
                        capture_fps = (len(wall_emit_times) - 1) / max(wall_span, 1e-6)
                    else:
                        capture_fps = frame_count / max(time.monotonic() - t0, 1e-6)
                    pending = writer.pending_segment_count(fast=True)
                    pq = writer.persist_queue_depth()
                    dropped = writer.dropped_frame_count()
                    seg_frames = int(os.environ.get("EGO_SEGMENT_MAX_FRAMES", "300"))
                    print(
                        f"captured={writer.next_frame_index} capture_fps={capture_fps:.2f} "
                        f"pending_segments={pending} persist_q={pq} dropped={dropped} "
                        f"strict_20hz=1 seg_max_frames={seg_frames} session={session_id}",
                        flush=True,
                    )
        else:
            while True:
                segment_frames = 0
                for ts_ns, capture_out, preview_out, imu6 in recorder.iter_synced_frames(
                    args.episode_seconds
                ):
                    _append_visual_frame(
                        writer,
                        preview_hub,
                        recorder,
                        timestamp_ns=ts_ns,
                        capture_out=capture_out,
                        preview_out=preview_out,
                        imu6=imu6,
                    )
                    frame_count += 1
                    segment_frames += 1
                    if frame_count % 30 == 0:
                        elapsed = max(time.monotonic() - t0, 1e-6)
                        capture_fps = frame_count / elapsed
                        pending = writer.pending_segment_count(fast=True)
                        pq = writer.persist_queue_depth()
                        dropped = writer.dropped_frame_count()
                        seg_frames = int(os.environ.get("EGO_SEGMENT_MAX_FRAMES", "300"))
                        print(
                            f"captured={writer.next_frame_index} capture_fps={capture_fps:.1f} "
                            f"pending_segments={pending} persist_q={pq} dropped={dropped} "
                            f"seg_max_frames={seg_frames} session={session_id}",
                            flush=True,
                        )
                print(
                    f"segment done local_frames={segment_frames} session={session_id}",
                    flush=True,
                )
    finally:
        writer.close()
        if heartbeat is not None:
            heartbeat.stop_periodic_heartbeat()
        recorder.stop()

    elapsed = max(time.monotonic() - t0, 1e-6)
    print(
        f"Done. {frame_count} frames in {elapsed:.1f}s "
        f"({frame_count / elapsed:.1f} fps) session={session_id}",
        flush=True,
    )


if __name__ == "__main__":
    main()
