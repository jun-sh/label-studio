"""OAK capture: local segment store + live preview only (upload via upload_segments CLI)."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
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
from ego_capture_studio.capture.ready_beep import play_capture_ready_beep
from ego_capture_studio.capture.segment_store import SegmentCaptureWriter, new_session_id

try:
    from ego_capture_studio.capture.oak_warm_session import (
        clear_warm_state,
        warm_duration_s,
        write_warm_state,
    )
except ImportError:
    from oak_warm_session import clear_warm_state, warm_duration_s, write_warm_state  # type: ignore[no-redef]

_SHUTDOWN = False
_WARM_IDLE_REQUEST = False
_WARM_RESUME_REQUEST = False


def _request_shutdown(signum: int, _frame) -> None:
    global _SHUTDOWN
    _SHUTDOWN = True
    print(f"capture_shutdown signal={signum}", flush=True)


def _request_warm_idle(signum: int, _frame) -> None:
    global _WARM_IDLE_REQUEST
    _WARM_IDLE_REQUEST = True
    print(f"capture_warm_idle signal={signum}", flush=True)


def _request_warm_resume(signum: int, _frame) -> None:
    global _WARM_RESUME_REQUEST
    _WARM_RESUME_REQUEST = True
    print(f"capture_warm_resume signal={signum}", flush=True)


def _oak_warm_idle_enabled() -> bool:
    return os.environ.get("EGO_OAK_WARM_IDLE", "1").strip().lower() in ("1", "true", "yes")

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


def _capture_stats_path(checkpoint_path: Path) -> Path:
    return checkpoint_path.parent / "capture_live_stats.json"


def _write_capture_live_stats(
    checkpoint_path: Path,
    *,
    frame_count: int,
    beep_epoch: float | None,
    capture_fps: float,
) -> None:
    path = _capture_stats_path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    effective_duration_s = int(frame_count / 30)
    payload = {
        "frame_count": int(frame_count),
        "effective_duration_s": effective_duration_s,
        "capture_fps": round(float(capture_fps), 3),
        "beep_epoch": beep_epoch,
        "updated_at": time.time(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(path)


def _clear_capture_live_stats(checkpoint_path: Path) -> None:
    """Reset timer anchor between capture sessions (warm resume keeps same process)."""
    _write_capture_live_stats(
        checkpoint_path,
        frame_count=0,
        beep_epoch=None,
        capture_fps=0.0,
    )


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
    primary_device_timestamp_ns: int | None = None,
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
        primary_device_timestamp_ns=primary_device_timestamp_ns,
        imu_raw_batch=imu_raw_batch,
    )


def _run_warmup_probe(recorder: Oak4pEgoRecorder, *, warm_resume: bool = False) -> tuple[Any, float]:
    if warm_resume:
        min_s = float(os.environ.get("EGO_WARM_RESUME_PROBE_MIN_S", "0.2"))
        max_s = float(os.environ.get("EGO_WARM_RESUME_PROBE_MAX_S", "0.6"))
        return recorder.record_episode_probe(min_s=min_s, max_s=max_s)
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


def _wait_h264_ready(recorder: Oak4pEgoRecorder, *, warm_resume: bool) -> None:
    if not (recorder.use_hw_h264 and _env_flag("OAK_H264_SEQUENTIAL", "0")):
        return
    streak_env = "EGO_WARM_RESUME_H264_STREAK" if warm_resume else "EGO_H264_READY_STREAK"
    max_env = "EGO_WARM_RESUME_H264_MAX_S" if warm_resume else "EGO_H264_READY_MAX_S"
    streak_default = "12" if warm_resume else "30"
    max_default = "2.0" if warm_resume else "2.0"
    streak_target = int(os.environ.get(streak_env, streak_default))
    max_s = float(os.environ.get(max_env, max_default))
    ready, streak = recorder.wait_h264_sequential_ready(
        max_s=max_s,
        streak_target=streak_target,
    )
    print(
        f"h264_sequential_ready ready={ready} streak={streak} warm_resume={int(warm_resume)} "
        f"max_s={max_s} target={streak_target}",
        flush=True,
    )
    if not ready:
        raise SystemExit(
            "H264 sequential pipeline not ready before beep; "
            "check OAK USB bandwidth and STRICT_* ring settings."
        )


def _run_warm_idle_loop(
    recorder: Oak4pEgoRecorder,
    preview_hub,
    preview_feed_stop: threading.Event | None,
    checkpoint_path: Path,
) -> bool:
    """Hold OAK connected until resume signal or warm timeout. Returns True to resume capture."""
    global _WARM_IDLE_REQUEST, _WARM_RESUME_REQUEST, _SHUTDOWN

    _clear_capture_live_stats(checkpoint_path)
    warm_s = warm_duration_s()
    expires_at = time.time() + warm_s
    write_warm_state(active=True, expires_at=expires_at)
    print(
        f"oak_warm_idle entered warm_s={warm_s:.0f} expires_at={expires_at:.3f}",
        flush=True,
    )
    _WARM_IDLE_REQUEST = False
    deadline = time.monotonic() + warm_s
    feed_fps = float(os.environ.get("PREVIEW_FPS", "8"))
    feed_interval = 1.0 / max(feed_fps, 0.5)
    try:
        while time.monotonic() < deadline:
            if _WARM_RESUME_REQUEST or _SHUTDOWN:
                break
            jpegs = recorder.drain_preview_jpegs_for_hub()
            if jpegs:
                preview_hub.offer_jpegs(jpegs)
            time.sleep(feed_interval)
    finally:
        reason = (
            "resume"
            if _WARM_RESUME_REQUEST
            else ("shutdown" if _SHUTDOWN else "timeout")
        )
        print(f"oak_warm_idle exit reason={reason}", flush=True)
        clear_warm_state()

    return bool(_WARM_RESUME_REQUEST and not _SHUTDOWN)


def _finish_recording_session(
    writer: SegmentCaptureWriter,
    recorder: Oak4pEgoRecorder,
    checkpoint_path: Path,
) -> None:
    remaining_imu = recorder.flush_remaining_imu_raw()
    if remaining_imu:
        writer.append_imu_raw(remaining_imu)
    writer.note_open_segment_health(**recorder.ingest_health())
    if recorder.use_hw_h264:
        recorder.prepare_h264_segment_boundary()
    writer.close()


def _start_preview_feeder(
    recorder: Oak4pEgoRecorder, preview_hub
) -> threading.Event:
    stop = threading.Event()
    feed_fps = float(os.environ.get("PREVIEW_FPS", "8"))

    def _run() -> None:
        interval = 1.0 / max(feed_fps, 0.5)
        while not stop.is_set() and not _SHUTDOWN:
            jpegs = recorder.drain_preview_jpegs_for_hub()
            if jpegs:
                preview_hub.offer_jpegs(jpegs)
            stop.wait(interval)

    threading.Thread(target=_run, name="preview-feeder", daemon=True).start()
    return stop


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


def _capture_sync_mode_label() -> str:
    from ego_capture_studio.capture.capture_h264_production import (
        capture_sync_mode,
        h264_production_backend_enabled,
    )

    if h264_production_backend_enabled():
        return capture_sync_mode() or "fsync_quad"
    return "egoverse_30hz"


def main() -> None:
    global _SHUTDOWN, _WARM_IDLE_REQUEST, _WARM_RESUME_REQUEST
    _SHUTDOWN = False
    _WARM_IDLE_REQUEST = False
    _WARM_RESUME_REQUEST = False
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    if _oak_warm_idle_enabled():
        signal.signal(signal.SIGUSR1, _request_warm_idle)
        signal.signal(signal.SIGUSR2, _request_warm_resume)

    from ego_capture_studio.capture.capture_h264_production import install_h264_production_backend

    install_h264_production_backend(sys.modules[__name__])

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
    p.add_argument(
        "--episode-seconds",
        type=float,
        default=0.0,
        help="Episode cap in seconds; 0 uses EGO_STRICT_EPISODE_SECONDS (default 86400 / 24h)",
    )
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
    device_fps = int(os.environ.get("OAK_DEVICE_FPS", "30"))
    default_task = args.task

    preview_hub = start_preview_stack()
    recorder = Oak4pEgoRecorder(
        fps=args.fps,
        device_fps=device_fps,
        imu_hz=args.imu_hz,
        enable_imu=enable_imu,
        force_imu=force_imu,
    )
    preview_feed_stop: threading.Event | None = None
    heartbeat: FrameStreamUploader | None = None
    warm_resume = False
    total_frame_count = 0
    t0 = time.monotonic()

    try:
        while True:
            _WARM_IDLE_REQUEST = False
            _WARM_RESUME_REQUEST = False

            if not warm_resume:
                recorder.connect()
                _require_capture_codec(recorder)
                preview_feed_stop = _start_preview_feeder(recorder, preview_hub)

            ck_session, ck_task = _load_checkpoint_session(checkpoint_path)
            session_id = new_session_id() if warm_resume else (
                ck_session or os.environ.get("EGO_CAPTURE_SESSION_ID") or new_session_id()
            )
            task = ck_task or default_task
            os.environ.setdefault("EGO_SEGMENT_ROOT", args.segment_root)
            os.environ["EGO_CAPTURE_SESSION_ID"] = session_id

            writer = SegmentCaptureWriter.from_env(task=task, checkpoint_path=checkpoint_path)
            if recorder.use_hw_h264:
                def _pre_segment_rotate(w=writer, rec=recorder) -> None:
                    w.note_open_segment_health(**rec.ingest_health())
                    rec.prepare_h264_segment_boundary()

                def _post_segment_rotate(rec=recorder) -> None:
                    rec.begin_segment_health_window()

                writer.register_pre_segment_rotate_hook(_pre_segment_rotate)
                writer.register_post_segment_rotate_hook(_post_segment_rotate)

            if not warm_resume:
                resumed_emit: int | None = None
                if not _env_flag("EGO_STRICT_EMIT_RESUME", "0"):
                    try:
                        _strict_emit_path(checkpoint_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                else:
                    resumed_emit = _load_strict_emit_ts_ns(checkpoint_path)
                    try:
                        ck_raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                        next_idx = int(ck_raw.get("nextFrameIndex", 0))
                    except (OSError, json.JSONDecodeError, TypeError, ValueError):
                        next_idx = 0
                    if resumed_emit is not None and next_idx <= 0:
                        try:
                            _strict_emit_path(checkpoint_path).unlink(missing_ok=True)
                        except OSError:
                            pass
                        resumed_emit = None
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

                if (
                    not args.no_heartbeat
                    and args.heartbeat_url
                    and FrameStreamUploader is not None
                ):
                    heartbeat = FrameStreamUploader(
                        args.heartbeat_url, checkpoint_path=checkpoint_path
                    )
                    heartbeat.session_id = session_id
                if heartbeat is not None:
                    heartbeat.session_id = session_id
                    _kick_heartbeat(heartbeat)
            elif heartbeat is not None:
                heartbeat.session_id = session_id
                _kick_heartbeat(heartbeat)

            if warm_resume:
                print(f"capture_warm_resume session={session_id}", flush=True)

            _clear_capture_live_stats(checkpoint_path)
            wall_emit_times: deque[float] = deque(maxlen=120)
            probe, probe_s = _run_warmup_probe(recorder, warm_resume=warm_resume)
            if probe.frame_count() == 0:
                raise SystemExit("No frames captured during probe; check OAK device and USB.")
            print(
                f"probe_duration_s={probe_s:.3f} warm_resume={int(warm_resume)}",
                flush=True,
            )
            _wait_h264_ready(recorder, warm_resume=warm_resume)
            beep_wall_epoch = time.time()
            recorder.begin_segment_health_window()
            play_capture_ready_beep()
            _write_capture_live_stats(
                checkpoint_path,
                frame_count=writer.next_frame_index,
                beep_epoch=beep_wall_epoch,
                capture_fps=0.0,
            )
            print(
                f"capture-only session={session_id} segment_root={args.segment_root} "
                f"warm_resume={int(warm_resume)} "
                f"fps_target={args.fps} device_fps={device_fps} imu_hz={args.imu_hz} "
                f"hw_jpeg={recorder.use_hw_jpeg} hw_h264={recorder.use_hw_h264} "
                f"sync_mode={_capture_sync_mode_label()} "
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

            frame_count = 0
            frame_iter = recorder.iter_strict_sync_frames(
                strict_duration_s,
                interval_ms=interval_ms,
                imu_interpolate=imu_interpolate,
                shutdown_check=lambda: _SHUTDOWN or _WARM_IDLE_REQUEST,
            )
            for frame in frame_iter:
                if _SHUTDOWN or _WARM_IDLE_REQUEST:
                    print(
                        f"capture_loop exit shutdown={int(_SHUTDOWN)} warm_idle={int(_WARM_IDLE_REQUEST)}",
                        flush=True,
                    )
                    break
                if len(frame) == 6:
                    ts_ns, capture_out, preview_out, imu6, cam_offsets, primary_dev_ts = frame
                else:
                    ts_ns, capture_out, preview_out, imu6, cam_offsets = frame
                    primary_dev_ts = None
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
                    primary_device_timestamp_ns=primary_dev_ts,
                    imu_raw_batch=imu_raw_batch,
                )
                frame_count += 1
                total_frame_count += 1
                wall_emit_times.append(emit_mono)
                if frame_count % 100 == 0 and recorder._strict_last_emit_ts_ns is not None:
                    _persist_strict_emit_ts_ns(
                        checkpoint_path, int(recorder._strict_last_emit_ts_ns)
                    )
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
                    writer.note_open_segment_health(**recorder.ingest_health())
                    _write_capture_live_stats(
                        checkpoint_path,
                        frame_count=writer.next_frame_index,
                        beep_epoch=beep_wall_epoch,
                        capture_fps=capture_fps,
                    )

            _finish_recording_session(writer, recorder, checkpoint_path)

            if _SHUTDOWN or not (_oak_warm_idle_enabled() and _WARM_IDLE_REQUEST):
                break

            if not _run_warm_idle_loop(
                recorder, preview_hub, preview_feed_stop, checkpoint_path
            ):
                break
            warm_resume = True

    finally:
        clear_warm_state()
        if preview_feed_stop is not None:
            preview_feed_stop.set()
        try:
            recorder.stop()
        except Exception as exc:
            print(f"recorder.stop warning: {exc}", flush=True)
        if heartbeat is not None:
            heartbeat.stop_periodic_heartbeat()

    elapsed = max(time.monotonic() - t0, 1e-6)
    print(
        f"Done. {total_frame_count} frames in {elapsed:.1f}s "
        f"({total_frame_count / elapsed:.1f} fps)",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"capture_fatal: {exc}", flush=True)
        raise
