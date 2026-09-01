"""OAK capture: local segment store + live preview only (upload via upload_segments CLI)."""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from collections import deque
from pathlib import Path
from typing import Any

from ego_capture_studio.capture.camera_intrinsics import (
    INTRINSICS_STATUS_INVALID,
    is_intrinsics_valid,
)
from ego_capture_studio.capture.intrinsics_store import (
    intrinsics_audit_line,
    intrinsics_strict_required,
    require_valid_intrinsics,
)
from ego_capture_studio.capture.ego_spec import OAK_CAPTURE_FPS, OAK_CAPTURE_IMU_HZ
from ego_capture_studio.capture.frame_jpeg_codec import (
    configure_opencv_threads,
    log_jpeg_encoder_info,
)
from ego_capture_studio.capture.oak_4p_capture import Oak4pEgoRecorder
from ego_capture_studio.capture.preview_server import start_preview_stack
from ego_capture_studio.capture.segment_store import SegmentCaptureWriter, new_session_id

_SHUTDOWN = False


def _request_shutdown(signum: int, _frame) -> None:
    global _SHUTDOWN
    _SHUTDOWN = True
    print(f"capture_shutdown signal={signum}", flush=True)

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


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def _capture_codec_mode(recorder: Oak4pEgoRecorder) -> str:
    if recorder.use_hw_h264:
        return "h264"
    if recorder.use_hw_jpeg:
        return "jpeg"
    return "bgr"


def _require_capture_codec(recorder: Oak4pEgoRecorder) -> None:
    """Validate capture codec against env profile (JPEG production vs Track2 H.264 MCAP)."""
    jpeg_only = _env_flag("EGO_CAPTURE_JPEG_ONLY", "1")
    mode = _capture_codec_mode(recorder)
    if jpeg_only:
        if mode != "jpeg":
            raise SystemExit(
                "capture codec must be HW JPEG (OAK_HW_JPEG=1, OAK_H264=0). "
                f"got hw_jpeg={recorder.use_hw_jpeg} hw_h264={recorder.use_hw_h264}. "
                "Fix ~/.config/ego-station.env.d/station.conf and restart capture."
            )
        return
    if mode == "h264":
        if not _env_flag("SEGMENT_MCAP", "0"):
            raise SystemExit(
                "Track2 VPU H.264 capture requires SEGMENT_MCAP=1. "
                f"got hw_h264={recorder.use_hw_h264} SEGMENT_MCAP unset."
            )
        return
    raise SystemExit(
        f"unsupported capture codec mode={mode!r} "
        f"(hw_jpeg={recorder.use_hw_jpeg} hw_h264={recorder.use_hw_h264}). "
        "Set EGO_CAPTURE_JPEG_ONLY=1 for JPEG or OAK_H264=1+SEGMENT_MCAP=1 for Track2."
    )


def _append_capture_frame(
    writer: SegmentCaptureWriter,
    preview_hub,
    recorder: Oak4pEgoRecorder,
    *,
    timestamp_ns: int,
    capture_out: dict,
    preview_out: dict,
    imu6,
    camera_ts_offset_ns: dict[str, int] | None = None,
    imu_raw_batch: list | tuple | None = None,
) -> None:
    mode = _capture_codec_mode(recorder)
    if mode not in ("jpeg", "h264"):
        raise RuntimeError(f"only HW JPEG or VPU H.264 capture is supported (mode={mode})")
    if mode == "jpeg":
        preview_hub.offer_jpegs(capture_out)
    if preview_out:
        preview_hub.offer_jpegs(preview_out)
    writer.append_frame(
        timestamp_ns=timestamp_ns,
        camera_jpegs=capture_out,
        imu6=imu6,
        camera_ts_offset_ns=camera_ts_offset_ns,
        imu_raw_batch=imu_raw_batch,
    )


def _run_warmup_probe(recorder: Oak4pEgoRecorder) -> tuple[Any, float]:
    fixed = os.environ.get("EGO_PROBE_FIXED_S", "").strip()
    if fixed:
        t0 = time.monotonic()
        probe = recorder.record_episode(float(fixed))
        return probe, time.monotonic() - t0
    if os.environ.get("EGO_PROBE_ADAPTIVE", "1").strip().lower() in ("0", "false", "no"):
        t0 = time.monotonic()
        probe = recorder.record_episode(3.0)
        return probe, time.monotonic() - t0
    min_s = float(os.environ.get("EGO_PROBE_MIN_S", "0.8"))
    max_s = float(os.environ.get("EGO_PROBE_MAX_S", "3.0"))
    return recorder.record_episode_probe(min_s=min_s, max_s=max_s)


def _kick_heartbeat(heartbeat: FrameStreamUploader | None) -> None:
    if heartbeat is None:
        return
    host = os.environ.get("DATALAB_CAPTURE_HOST", "10.10.10.214")
    async_hb = os.environ.get("EGO_HEARTBEAT_ASYNC", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    if async_hb:
        heartbeat.heartbeat_async(host=host)
        return
    try:
        heartbeat.heartbeat(host=host)
    except Exception as exc:
        print(f"heartbeat warning: {exc}", flush=True)


def _station_id() -> str:
    return os.environ.get("EGO_STATION_ID", "ego-001").strip() or "ego-001"


def main() -> None:
    global _SHUTDOWN
    _SHUTDOWN = False
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    station = _station_id()
    p = argparse.ArgumentParser(
        description="OAK edge capture (Scheme A): segments on disk + MJPEG preview, no live upload.",
    )
    p.add_argument(
        "--heartbeat-url",
        type=str,
        default=os.environ.get(
            "DATALAB_HEARTBEAT_URL",
            f"http://10.10.10.34:8080/lerobot/api/collection/stations/{station}/upload",
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
            f"/home/server/cache/{station}/segments/checkpoint.json",
        ),
    )
    p.add_argument(
        "--segment-root",
        type=str,
        default=os.environ.get("EGO_SEGMENT_ROOT", f"/home/server/cache/{station}/segments"),
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
    interval_ms = int(os.environ.get("EGO_FRAME_INTERVAL_MS", "33"))
    imu_interpolate = _env_flag("EGO_IMU_INTERPOLATE", "1")

    preview_hub = start_preview_stack()

    ck_session, ck_task = _load_checkpoint_session(checkpoint_path)
    session_id = ck_session or os.environ.get("EGO_CAPTURE_SESSION_ID") or new_session_id()
    task = ck_task or args.task
    os.environ.setdefault("EGO_SEGMENT_ROOT", args.segment_root)
    os.environ["EGO_CAPTURE_SESSION_ID"] = session_id

    writer = SegmentCaptureWriter.from_env(task=task, checkpoint_path=checkpoint_path)

    heartbeat: FrameStreamUploader | None = None
    if not args.no_heartbeat and args.heartbeat_url and FrameStreamUploader is not None:
        heartbeat = FrameStreamUploader(args.heartbeat_url, checkpoint_path=checkpoint_path)
        heartbeat.session_id = session_id

    device_fps = int(os.environ.get("OAK_DEVICE_FPS", "30"))
    recorder = Oak4pEgoRecorder(
        fps=args.fps,
        device_fps=device_fps,
        imu_hz=args.imu_hz,
        enable_imu=enable_imu,
        force_imu=force_imu,
    )
    recorder.connect()
    _require_capture_codec(recorder)

    resumed_emit = _load_strict_emit_ts_ns(checkpoint_path)
    if resumed_emit is not None:
        recorder._strict_last_emit_ts_ns = int(resumed_emit)
        print(f"strict_emit_resume ts_ns={resumed_emit}", flush=True)

    try:
        intrinsics_doc = recorder.build_session_camera_intrinsics_document()
        if intrinsics_strict_required():
            require_valid_intrinsics(intrinsics_doc)
        intrinsics_path = writer.write_session_camera_intrinsics(intrinsics_doc)
        calib_src = intrinsics_doc.get("calibration_source") or "unknown"
        if is_intrinsics_valid(intrinsics_doc):
            print(
                f"camera_intrinsics OK {intrinsics_audit_line(intrinsics_doc, path=intrinsics_path)}",
                flush=True,
            )
        else:
            reasons = intrinsics_doc.get("invalid_reasons") or []
            print(
                f"camera_intrinsics {INTRINSICS_STATUS_INVALID} written={intrinsics_path} "
                f"calibration_source={calib_src} reasons={reasons} "
                f"device_mxid={intrinsics_doc.get('device_mxid')}",
                flush=True,
            )
    except Exception as exc:
        print(f"camera_intrinsics FATAL: {exc}", flush=True)
        if intrinsics_strict_required():
            raise SystemExit(1) from exc
        print(f"camera_intrinsics warning: {exc}", flush=True)

    if heartbeat is not None:
        _kick_heartbeat(heartbeat)

    frame_count = 0
    t0 = time.monotonic()
    wall_emit_times: deque[float] = deque(maxlen=120)
    try:
        probe, probe_s = _run_warmup_probe(recorder)
        if probe.frame_count() == 0:
            raise SystemExit("No frames captured during probe; check OAK device and USB.")
        print(f"probe_duration_s={probe_s:.3f}", flush=True)
        print(
            f"capture-only session={session_id} segment_root={args.segment_root} "
            f"fps_target={args.fps} device_fps={device_fps} imu_hz={args.imu_hz} "
            f"hw_jpeg={recorder.use_hw_jpeg} hw_h264={recorder.use_hw_h264} "
            f"sync_mode=egoverse_30hz "
            f"interval_ms={interval_ms} imu_interpolate={int(imu_interpolate)}",
            flush=True,
        )

        strict_duration_s = float(os.environ.get("EGO_STRICT_EPISODE_SECONDS", "86400"))
        if args.episode_seconds and args.episode_seconds > 0:
            strict_duration_s = min(strict_duration_s, float(args.episode_seconds))
        print(
            f"episode_limit_s={strict_duration_s:.1f} "
            f"(cli={args.episode_seconds}, env={os.environ.get('EGO_STRICT_EPISODE_SECONDS', '86400')})",
            flush=True,
        )
        frame_iter = recorder.iter_strict_sync_frames(
            strict_duration_s,
            interval_ms=interval_ms,
            imu_interpolate=imu_interpolate,
            shutdown_check=lambda: _SHUTDOWN,
        )
        for ts_ns, capture_out, preview_out, imu6, cam_offsets in frame_iter:
            if _SHUTDOWN:
                print("capture_shutdown exit frame loop", flush=True)
                break
            imu_raw_batch = recorder.pop_pending_imu_raw()
            emit_mono = time.monotonic()
            _append_capture_frame(
                writer,
                preview_hub,
                recorder,
                timestamp_ns=ts_ns,
                capture_out=capture_out,
                preview_out=preview_out,
                imu6=imu6,
                camera_ts_offset_ns=cam_offsets,
                imu_raw_batch=imu_raw_batch,
            )
            frame_count += 1
            wall_emit_times.append(emit_mono)
            if frame_count % 100 == 0 and recorder._strict_last_emit_ts_ns is not None:
                _persist_strict_emit_ts_ns(checkpoint_path, int(recorder._strict_last_emit_ts_ns))
            if frame_count % 30 == 0:
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
                    f"sync_mode=egoverse_30hz seg_max_frames={seg_frames} session={session_id}",
                    flush=True,
                )
    finally:
        remaining_imu = recorder.flush_remaining_imu_raw()
        if remaining_imu:
            writer.append_imu_raw(remaining_imu)
        try:
            recorder.stop()
        except Exception as exc:
            print(f"recorder.stop warning: {exc}", flush=True)
        writer.close()
        if heartbeat is not None:
            heartbeat.stop_periodic_heartbeat()

    elapsed = max(time.monotonic() - t0, 1e-6)
    print(
        f"Done. {frame_count} frames in {elapsed:.1f}s "
        f"({frame_count / elapsed:.1f} fps) session={session_id}",
        flush=True,
    )


if __name__ == "__main__":
    main()
