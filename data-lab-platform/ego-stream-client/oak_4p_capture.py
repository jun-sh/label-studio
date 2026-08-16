"""OAK-4P-New / OAK-FFC-4P four-camera capture with optional IMU (DepthAI)."""

from __future__ import annotations

import os
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from ego_capture_studio.capture.buffers import EpisodeBuffers
from ego_capture_studio.capture.camera_map import (
    ALL_OAK_SOCKETS,
    DEPTH_OAK_SOCKET as _TOPO_DEPTH_OAK_SOCKET,
    HEAD_RIGHT_OAK_SOCKET as _TOPO_HEAD_RIGHT_OAK_SOCKET,
    OAK_SOCKET_TO_LEROBOT_VIDEO,
    PRIMARY_OAK_SOCKET,
    STRICT_CAUSAL_OAK_SOCKETS as _TOPO_STRICT_CAUSAL_OAK_SOCKETS,
    topology_snapshot,
)
from ego_capture_studio.capture.camera_intrinsics import (
    build_camera_intrinsics_document,
    read_calibration_from_device,
    read_camera_intrinsics_entry,
)
from ego_capture_studio.capture.ego_spec import (
    OAK_CAPTURE_FPS,
    OAK_CAPTURE_IMU_HZ,
    OAK_DEFAULT_FRAME_HEIGHT,
    OAK_DEFAULT_FRAME_WIDTH,
)

# AR0234 module: sensor fixed 1200P; ISP scale 2/3 -> 1280x800 (manufacturer FPS recipe).
OAK_SENSOR_RES_KEY = "1200"
OAK_ISP_SCALE_NUM = int(os.environ.get("OAK_ISP_SCALE_NUM", "2"))
OAK_ISP_SCALE_DEN = int(os.environ.get("OAK_ISP_SCALE_DEN", "3"))
OAK_USE_IMAGEMANIP = os.environ.get("OAK_USE_IMAGEMANIP", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
OAK_GPIO_FSYNC = os.environ.get("OAK_GPIO_FSYNC", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
OAK_LOOSE_SYNC = os.environ.get("OAK_LOOSE_SYNC", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
OAK_PREVIEW_MAX_EDGE = int(os.environ.get("PREVIEW_MAX_EDGE", "640"))
# Device-side MJPEG via VideoEncoder (removes host TurboJPEG on capture path).
OAK_HW_JPEG = os.environ.get("OAK_HW_JPEG", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
OAK_MJPEG_QUALITY = int(os.environ.get("DATALAB_JPEG_QUALITY", "50"))
# Device sensor/encoder FPS (manufacturer demo uses 30); capture_fps may be lower with persist.
OAK_DEVICE_FPS = int(os.environ.get("OAK_DEVICE_FPS", "30"))
# Second MJPEG encoder per cam for preview doubles USB load; reuse capture JPEG by default.
OAK_HW_PREVIEW = os.environ.get("OAK_HW_PREVIEW", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Low-res MJPEG sidecar for collection UI when main path is H.264 (off by default: extra encoders/USB).
OAK_HW_PREVIEW_H264 = os.environ.get("OAK_HW_PREVIEW_H264", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Phase-2 POC: H.264 bitstream per cam (local segment only; ingest still expects JPEG upload).
OAK_H264 = os.environ.get("OAK_H264", "0").strip().lower() in ("1", "true", "yes")
OAK_H264_BITRATE_KBPS = int(os.environ.get("OAK_H264_BITRATE_KBPS", "6000"))
_h264_seq_env = os.environ.get("OAK_H264_SEQUENTIAL", "").strip().lower()
OAK_H264_SEQUENTIAL = (
    _h264_seq_env in ("1", "true", "yes")
    if _h264_seq_env
    else OAK_H264
)
OAK_H264_KEYFRAME_FREQUENCY = max(
    1,
    int(os.environ.get("OAK_H264_KEYFRAME_FREQUENCY", str(OAK_DEVICE_FPS))),
)
OAK_CAM_QUEUE_MAX = max(4, int(os.environ.get("OAK_CAM_QUEUE_MAX", "32" if OAK_H264 else "8")))
OAK_PREVIEW_QUEUE_MAX = max(2, int(os.environ.get("OAK_PREVIEW_QUEUE_MAX", "4")))
# Phase-2: depth socket capture rate divisor vs RGB (1=every frame, 2=half, etc.)
OAK_DEPTH_FRAME_DIVISOR = max(1, int(os.environ.get("OAK_DEPTH_FRAME_DIVISOR", "1")))


def ego_frame_interval_ns() -> int:
    """Grid interval aligned to OAK_DEVICE_FPS (default 30 Hz = 33.333ms)."""
    ms_override = os.environ.get("EGO_FRAME_INTERVAL_MS", "").strip()
    if ms_override:
        return int(ms_override) * 1_000_000
    return 1_000_000_000 // max(1, OAK_DEVICE_FPS)


EGO_FRAME_INTERVAL_MS = ego_frame_interval_ns() // 1_000_000
EGO_IMU_INTERPOLATE = os.environ.get("EGO_IMU_INTERPOLATE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
STRICT_CAM_RING_LEN = max(48, int(os.environ.get("STRICT_CAM_RING_LEN", "64")))
STRICT_PRIMARY_RING_LEN = max(64, int(os.environ.get("STRICT_PRIMARY_RING_LEN", "96")))
STRICT_RGB_RING_LEN = max(48, int(os.environ.get("STRICT_RGB_RING_LEN", "64")))
STRICT_DEPTH_RING_LEN = max(12, int(os.environ.get("STRICT_DEPTH_RING_LEN", "24")))
DEPTH_OAK_SOCKET = os.environ.get("DEPTH_OAK_SOCKET", _TOPO_DEPTH_OAK_SOCKET).strip() or _TOPO_DEPTH_OAK_SOCKET
HEAD_RIGHT_OAK_SOCKET = (
    os.environ.get("HEAD_RIGHT_OAK_SOCKET", _TOPO_HEAD_RIGHT_OAK_SOCKET).strip()
    or _TOPO_HEAD_RIGHT_OAK_SOCKET
)
_env_causal = os.environ.get("STRICT_CAUSAL_OAK_SOCKETS", "").strip()
STRICT_CAUSAL_OAK_SOCKETS = (
    frozenset(s.strip() for s in _env_causal.split(",") if s.strip())
    if _env_causal
    else _TOPO_STRICT_CAUSAL_OAK_SOCKETS
)
EGO_STRICT_DEPTH_CAUSAL = os.environ.get("EGO_STRICT_DEPTH_CAUSAL", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Device timestamp jump / grid misalignment guards for strict sync grid.
STRICT_TS_JUMP_NS = int(os.environ.get("STRICT_TS_JUMP_NS", "100000000"))
STRICT_MAX_CAM_OFFSET_NS = int(os.environ.get("STRICT_MAX_CAM_OFFSET_NS", "50000000"))
STRICT_REANCHOR_WARMUP_TICKS = max(0, int(os.environ.get("STRICT_REANCHOR_WARMUP_TICKS", "10")))
STRICT_RGB_YIELD_MAX_MS = float(os.environ.get("STRICT_RGB_YIELD_MAX_MS", "16.0"))
STRICT_DEPTH_YIELD_MAX_MS = float(os.environ.get("STRICT_DEPTH_YIELD_MAX_MS", "18.0"))
STRICT_SYNC_MISS_MAX = max(1, int(os.environ.get("STRICT_SYNC_MISS_MAX", "4")))
STRICT_IMU_BUFFER_MAX = max(256, int(os.environ.get("STRICT_IMU_BUFFER_MAX", "2000")))
_STRICT_PRIMARY_KEY_SUBSTRS = ("front_left", "head_left")
_STRICT_RGB_KEY_SUBSTRS = ("front_right", "rear_right", "head_right", "camera_02")
_STRICT_DEPTH_KEY_SUBSTRS = ("depth_left", "rear_left", "depth_head")


def _strict_channel_for_key(key: str) -> str | None:
    if any(s in key for s in _STRICT_PRIMARY_KEY_SUBSTRS):
        return "primary"
    if any(s in key for s in _STRICT_DEPTH_KEY_SUBSTRS):
        return "depth"
    if any(s in key for s in _STRICT_RGB_KEY_SUBSTRS):
        return "rgb"
    return None
STRICT_GRID_SLACK_NS = int(os.environ.get("STRICT_GRID_SLACK_NS", "35000000"))
STRICT_REANCHOR_LOG = os.environ.get("STRICT_REANCHOR_LOG", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)

FSYNC_GPIO_SCRIPT = """# coding=utf-8
import time
import GPIO

fps = %f

calib = Device.readCalibration2().getEepromData()
boardRev = calib.boardRev

revision = -1
if len(boardRev) >= 2 and boardRev[0] == 'R':
    revision = int(boardRev[1])

GPIO_FSIN_2LANE = 41
GPIO_FSIN_4LANE = 40
GPIO_FSIN_MODE_SELECT = 6

if revision >= 6:
    GPIO_FSIN_2LANE = 41
    GPIO_FSIN_4LANE = 42
    GPIO_FSIN_MODE_SELECT = 38

GPIO.setup(GPIO_FSIN_2LANE, GPIO.OUT)
GPIO.write(GPIO_FSIN_2LANE, 0)
GPIO.setup(GPIO_FSIN_4LANE, GPIO.IN)
GPIO.setup(GPIO_FSIN_MODE_SELECT, GPIO.OUT)
GPIO.write(GPIO_FSIN_MODE_SELECT, 1)

period = 1 / fps
active = 0.001
overhead = 0.003

node.warn(f'FSYNC GPIO script, rev={boardRev}, fps={fps}')

while True:
    GPIO.write(GPIO_FSIN_2LANE, 1)
    time.sleep(active)
    GPIO.write(GPIO_FSIN_2LANE, 0)
    time.sleep(period - active - overhead)
"""

MONO_RES_OPTS: dict[str, Any] = {}
COLOR_RES_OPTS: dict[str, Any] = {}
CAM_SOCKET_OPTS: dict[str, Any] = {}
CAM_SOCKET_TO_NAME: dict[str, str] = {}


def _ensure_depthai() -> Any:
    try:
        import depthai as dai  # noqa: N812
    except ImportError as e:
        raise RuntimeError(
            "depthai is required for OAK capture. Install optional extra: ego_capture"
        ) from e
    return dai


def _init_depthai_maps(dai: Any) -> None:
    global MONO_RES_OPTS, COLOR_RES_OPTS, CAM_SOCKET_OPTS, CAM_SOCKET_TO_NAME
    if MONO_RES_OPTS:
        return
    MONO_RES_OPTS.update(
        {
            "400": dai.MonoCameraProperties.SensorResolution.THE_400_P,
            "480": dai.MonoCameraProperties.SensorResolution.THE_480_P,
            "720": dai.MonoCameraProperties.SensorResolution.THE_720_P,
            "800": dai.MonoCameraProperties.SensorResolution.THE_800_P,
            "1200": dai.MonoCameraProperties.SensorResolution.THE_1200_P,
        }
    )
    COLOR_RES_OPTS.update(
        {
            "720": dai.ColorCameraProperties.SensorResolution.THE_720_P,
            "800": dai.ColorCameraProperties.SensorResolution.THE_800_P,
            "1080": dai.ColorCameraProperties.SensorResolution.THE_1080_P,
            "1200": dai.ColorCameraProperties.SensorResolution.THE_1200_P,
        }
    )
    CAM_SOCKET_OPTS.update(
        {
            "CAM_A": dai.CameraBoardSocket.CAM_A,
            "CAM_B": dai.CameraBoardSocket.CAM_B,
            "CAM_C": dai.CameraBoardSocket.CAM_C,
            "CAM_D": dai.CameraBoardSocket.CAM_D,
        }
    )
    CAM_SOCKET_TO_NAME.update(
        {
            "RGB": "CAM_A",
            "LEFT": "CAM_B",
            "RIGHT": "CAM_C",
            "CAM_A": "CAM_A",
            "CAM_B": "CAM_B",
            "CAM_C": "CAM_C",
            "CAM_D": "CAM_D",
        }
    )


def parse_board_revision(board_rev: str) -> int:
    m = re.match(r"R(\d+)", board_rev or "")
    return int(m.group(1)) if m else -1


def infer_cam_props(sensor_name: str, supported_types: list) -> dict[str, Any]:
    """All color sensors: fixed 1200P + ISP 2/3 (hardware downscale, no host resize)."""
    name = sensor_name.upper()
    type_names = {t.name for t in supported_types}
    is_color = "COLOR" in type_names or "RGB" in type_names
    if "OV9282" in name or "OV9281" in name:
        return {"color": False, "res": "800"}
    if is_color:
        return {
            "color": True,
            "res": OAK_SENSOR_RES_KEY,
            "isp_scale": (OAK_ISP_SCALE_NUM, OAK_ISP_SCALE_DEN),
        }
    return {"color": False, "res": "800"}


def _device_ts_ns(ts_device: Any) -> int:
    return int(float(ts_device.total_seconds()) * 1e9)


def _frame_from_packet(pkt: Any) -> np.ndarray | None:
    """BGR frame from device; no host color conversion."""
    frame = pkt.getCvFrame()
    if frame is None or frame.size == 0:
        return None
    return frame


def _jpeg_from_packet(pkt: Any) -> bytes | None:
    """Hardware MJPEG bitstream from VideoEncoder."""
    try:
        data = pkt.getData()
    except Exception:
        return None
    if data is None:
        return None
    out = bytes(data)
    return out if len(out) > 0 else None


def _packet_frame_size(pkt: Any, *, hw_jpeg: bool) -> tuple[int, int] | None:
    if hw_jpeg:
        return None
    frame = _frame_from_packet(pkt)
    if frame is None:
        return None
    h, w = int(frame.shape[0]), int(frame.shape[1])
    return h, w


def _scaled_size(width: int, height: int, max_edge: int) -> tuple[int, int]:
    longest = max(width, height)
    if longest <= max_edge:
        return width, height
    scale = max_edge / float(longest)
    return max(2, int(width * scale)), max(2, int(height * scale))


def _read_camera_intrinsics(calib: Any, socket: Any, width: int, height: int) -> dict[str, Any]:
    entry = read_camera_intrinsics_entry(calib, socket, width, height)
    # Backward-compatible alias used by older ego buffers.
    entry["distortion_model_name"] = entry.get("distortion_model", "fisheye")
    return entry


@dataclass(frozen=True)
class _CamRingSample:
    ts_ns: int
    payload: bytes | np.ndarray


class Oak4pEgoRecorder:
    """Record four synchronized OAK cameras + optional IMU into EpisodeBuffers."""

    def __init__(
        self,
        *,
        fps: int = OAK_CAPTURE_FPS,
        device_fps: int | None = None,
        imu_hz: int = OAK_CAPTURE_IMU_HZ,
        enable_imu: bool = True,
        force_imu: bool = False,
    ) -> None:
        self.device_fps = int(device_fps if device_fps is not None else OAK_DEVICE_FPS)
        self.fps = int(fps)
        self.imu_hz = int(imu_hz)
        self.enable_imu = enable_imu
        self.force_imu = force_imu
        self._dai: Any = None
        self._device: Any = None
        self._cam_list: dict[str, dict[str, Any]] = {}
        self._cam_queues: dict[str, Any] = {}
        self._preview_queues: dict[str, Any] = {}
        self._preview_wh: tuple[int, int] = (0, 0)
        self._imu_queue: Any = None
        self._use_gpio_fsync = False
        self._calib: Any = None
        self._hw_jpeg = OAK_HW_JPEG and not OAK_H264
        self._hw_h264 = OAK_H264
        self._cams_warmed = False
        self._primary_tick = 0
        self._last_capture: dict[str, bytes] | dict[str, np.ndarray] = {}
        self._strict_grid_epoch_ns: int | None = None
        self._strict_last_emit_ts_ns: int | None = None
        self._intrinsics_document: dict[str, Any] | None = None
        self._calibration_source: str | None = None
        self._imu_flush_gyro_idx = 0
        self._imu_flush_accel_idx = 0
        self._pending_imu_raw: list[dict[str, Any]] = []
        self._strict_imu_buf: Any = None
        self._h264_stale_drops = 0

    def h264_stale_drop_count(self) -> int:
        return int(self._h264_stale_drops)

    def build_session_camera_intrinsics_document(self) -> dict[str, Any]:
        """EEPROM intrinsics for all connected cameras at ISP output resolution."""
        if self._calib is None or self._device is None:
            raise RuntimeError("call connect() before building camera intrinsics")
        if self._intrinsics_document is not None:
            return self._intrinsics_document

        dai = _ensure_depthai()
        cameras: dict[str, dict[str, Any]] = {}
        scale_num = int(OAK_ISP_SCALE_NUM)
        scale_den = int(OAK_ISP_SCALE_DEN) or 1
        for feat in self._device.getConnectedCameraFeatures():
            cam_name = CAM_SOCKET_TO_NAME.get(feat.socket.name)
            if cam_name is None or cam_name not in self._cam_list:
                continue
            lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[cam_name]
            w = max(2, int(round(int(feat.width) * scale_num / scale_den)))
            h = max(2, int(round(int(feat.height) * scale_num / scale_den)))
            cameras[lerobot_key] = {
                "socket": CAM_SOCKET_OPTS[cam_name],
                "width": w,
                "height": h,
                "oak_socket": cam_name,
            }

        if len(cameras) < len(self._cam_list):
            cap_w = int(OAK_DEFAULT_FRAME_WIDTH)
            cap_h = int(OAK_DEFAULT_FRAME_HEIGHT)
            for cam_name in self._cam_list:
                lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[cam_name]
                if lerobot_key in cameras:
                    continue
                cameras[lerobot_key] = {
                    "socket": CAM_SOCKET_OPTS[cam_name],
                    "width": cap_w,
                    "height": cap_h,
                    "oak_socket": cam_name,
                }

        doc = build_camera_intrinsics_document(
            self._calib,
            device_mxid=str(self._device.getMxId()),
            cameras=cameras,
            calibration_source=self._calibration_source,
        )
        doc["topology"] = topology_snapshot()
        self._intrinsics_document = doc
        return doc

    @property
    def strict_grid_epoch_ns(self) -> int | None:
        return self._strict_grid_epoch_ns

    @staticmethod
    def _strict_ring_len(oak_socket: str) -> int:
        if oak_socket == PRIMARY_OAK_SOCKET:
            return STRICT_PRIMARY_RING_LEN
        if oak_socket == DEPTH_OAK_SOCKET:
            return STRICT_DEPTH_RING_LEN
        return STRICT_RGB_RING_LEN

    @staticmethod
    def _nearest_ring_sample(ring: deque[_CamRingSample], target_ts_ns: int) -> _CamRingSample | None:
        if not ring:
            return None
        return min(ring, key=lambda s: abs(int(s.ts_ns) - int(target_ts_ns)))

    @classmethod
    def _uses_causal_primary_lock(cls, oak_socket: str) -> bool:
        if oak_socket in STRICT_CAUSAL_OAK_SOCKETS:
            return True
        if EGO_STRICT_DEPTH_CAUSAL and oak_socket == DEPTH_OAK_SOCKET:
            return True
        return False

    @staticmethod
    def _ring_sample_locked_to_primary(
        ring: deque[_CamRingSample], primary_ts_ns: int
    ) -> _CamRingSample | None:
        """Pick closest sample to primary; prefer frames at/after primary when available."""
        if not ring:
            return None
        target = int(primary_ts_ns)
        at_or_after = [s for s in ring if int(s.ts_ns) >= target]
        pool = at_or_after if at_or_after else list(ring)
        return min(pool, key=lambda s: abs(int(s.ts_ns) - target))

    @classmethod
    def _strict_max_offset_ns_for_oak(cls, oak_socket: str) -> int:
        if oak_socket == PRIMARY_OAK_SOCKET:
            return STRICT_MAX_CAM_OFFSET_NS
        if oak_socket == DEPTH_OAK_SOCKET:
            return int(STRICT_DEPTH_YIELD_MAX_MS * 1_000_000)
        return int(STRICT_RGB_YIELD_MAX_MS * 1_000_000)

    @classmethod
    def _strict_ring_sample(
        cls,
        ring: deque[_CamRingSample],
        target_ts_ns: int,
        max_abs_offset_ns: int,
        *,
        prefer_at_or_after: bool = False,
    ) -> _CamRingSample | None:
        """Best ring sample to grid target within per-channel sync budget."""
        if not ring:
            return None
        target = int(target_ts_ns)
        limit = int(max_abs_offset_ns)
        within = [
            s for s in ring if abs(int(s.ts_ns) - target) <= limit
        ]
        if not within:
            return None
        if prefer_at_or_after:
            causal = [s for s in within if int(s.ts_ns) >= target]
            pool = causal if causal else within
        else:
            pool = within
        return min(pool, key=lambda s: abs(int(s.ts_ns) - target))

    def _consume_h264_ring_sample(
        self,
        ring: deque[_CamRingSample],
        oak_socket: str,
        target_ts_ns: int,
    ) -> _CamRingSample | None:
        """FIFO H264 packet pick — preserves encoder GOP order (no subsample gaps)."""
        if not ring:
            return None
        target = int(target_ts_ns)
        limit = self._strict_max_offset_ns_for_oak(oak_socket)
        while len(ring) > 1:
            first = ring[0]
            if int(first.ts_ns) < target - limit:
                ring.popleft()
                self._h264_stale_drops += 1
            else:
                break
        if not ring:
            return None
        sample = ring[0]
        if abs(int(sample.ts_ns) - target) > limit:
            return None
        ring.popleft()
        return sample

    def _ring_sample_for_yield(
        self,
        ring: deque[_CamRingSample],
        oak_socket: str,
        target_ts_ns: int,
    ) -> _CamRingSample | None:
        if self._hw_h264 and OAK_H264_SEQUENTIAL:
            return self._consume_h264_ring_sample(ring, oak_socket, target_ts_ns)
        if self._uses_causal_primary_lock(oak_socket):
            return self._ring_sample_locked_to_primary(ring, target_ts_ns)
        max_off = self._strict_max_offset_ns_for_oak(oak_socket)
        return self._strict_ring_sample(
            ring,
            target_ts_ns,
            max_off,
            prefer_at_or_after=True,
        )

    @staticmethod
    def _align_epoch_to_device(ts_ns: int, interval_ns: int) -> int:
        ts = int(ts_ns)
        step = int(interval_ns)
        return ts - (ts % step)

    def _reanchor_strict_grid(
        self,
        cam_rings: dict[str, deque[_CamRingSample]],
        *,
        interval_ns: int,
        reason: str,
    ) -> int | None:
        primary_ring = cam_rings.get(PRIMARY_OAK_SOCKET)
        if not primary_ring:
            return None
        epoch_ns = self._align_epoch_to_device(int(primary_ring[-1].ts_ns), interval_ns)
        for oak in self._cam_list:
            cam_rings[oak].clear()
        self._strict_grid_epoch_ns = int(epoch_ns)
        if STRICT_REANCHOR_LOG:
            print(f"strict_sync_reanchor reason={reason} epoch_ns={epoch_ns}", flush=True)
        return int(epoch_ns)

    def _strict_needs_reanchor(
        self,
        *,
        primary_ts_ns: int,
        last_primary_ts_ns: int | None,
        grid_ticks_since_last_yield: int,
        interval_ns: int,
    ) -> str | None:
        if last_primary_ts_ns is None:
            return None
        ticks = max(1, int(grid_ticks_since_last_yield))
        delta = abs(int(primary_ts_ns) - int(last_primary_ts_ns))
        expected_ns = ticks * int(interval_ns)
        slack_ns = max(STRICT_TS_JUMP_NS, int(interval_ns) // 2)
        if delta > expected_ns + slack_ns:
            return f"primary_jump_ns={delta}"
        return None

    @staticmethod
    def _strict_exceeds_yield_limits(offsets: dict[str, int]) -> str | None:
        for key, off in offsets.items():
            ms = abs(int(off)) / 1e6
            ch = _strict_channel_for_key(key)
            if ch == "rgb" and ms > STRICT_RGB_YIELD_MAX_MS:
                return f"rgb_offset_ms={ms}"
            if ch == "depth" and ms > STRICT_DEPTH_YIELD_MAX_MS:
                return f"depth_offset_ms={ms}"
            if ms > 100.0:
                return f"huge_offset_ms={ms}"
        return None

    @property
    def use_hw_jpeg(self) -> bool:
        return self._hw_jpeg

    @property
    def use_hw_h264(self) -> bool:
        return self._hw_h264

    def connect(self) -> None:
        dai = _ensure_depthai()
        _init_depthai_maps(dai)
        self._dai = dai

        try:
            device = dai.Device()
        except RuntimeError as exc:
            if "INSUFFICIENT_PERMISSIONS" in str(exc):
                raise RuntimeError(
                    "OAK USB permissions missing. Configure udev for idVendor 03e7, then replug."
                ) from exc
            raise

        self._device = device
        self._calib, self._calibration_source = read_calibration_from_device(device)
        eeprom = self._calib.getEepromData()
        board_rev = eeprom.boardRev or ""
        revision = parse_board_revision(board_rev)
        self._use_gpio_fsync = revision >= 6 or "FFC-4P" in (eeprom.productName or "")

        cam_list: dict[str, dict[str, Any]] = {
            name: {
                "color": True,
                "res": OAK_SENSOR_RES_KEY,
                "isp_scale": (OAK_ISP_SCALE_NUM, OAK_ISP_SCALE_DEN),
            }
            for name in ALL_OAK_SOCKETS
        }
        sensor_names: dict[str, str] = {}
        for feat in device.getConnectedCameraFeatures():
            cam_name = CAM_SOCKET_TO_NAME.get(feat.socket.name)
            if cam_name is None:
                continue
            sensor_names[cam_name] = feat.sensorName
            cam_list[cam_name] = infer_cam_props(feat.sensorName, list(feat.supportedTypes))

        if len(sensor_names) < 4:
            device.close()
            raise RuntimeError(f"OAK device needs 4 cameras, found {len(sensor_names)}")

        self._cam_list = {k: cam_list[k] for k in sensor_names if k in cam_list}
        cap_w = int(OAK_DEFAULT_FRAME_WIDTH)
        cap_h = int(OAK_DEFAULT_FRAME_HEIGHT)
        self._preview_wh = _scaled_size(cap_w, cap_h, OAK_PREVIEW_MAX_EDGE)

        has_imu_hw = self._device_has_imu(device)
        imu_on = self.enable_imu and (self.force_imu or has_imu_hw)
        if self.force_imu and not has_imu_hw:
            imu_on = True

        pipeline = self._create_pipeline(imu_on, board_rev)
        pipeline.setXLinkChunkSize(0)
        device.startPipeline(pipeline)

        self._cam_queues = {
            name: device.getOutputQueue(name=name, maxSize=OAK_CAM_QUEUE_MAX, blocking=False)
            for name in self._cam_list
        }
        self._preview_queues = {}
        preview_streams = (not self._hw_jpeg and OAK_USE_IMAGEMANIP) or (
            self._hw_jpeg and OAK_HW_PREVIEW
        )
        if preview_streams:
            for name, props in self._cam_list.items():
                if props.get("color"):
                    stream = f"{name}_preview"
                    self._preview_queues[name] = device.getOutputQueue(
                        name=stream, maxSize=OAK_PREVIEW_QUEUE_MAX, blocking=False
                    )
        self._imu_queue = (
            device.getOutputQueue("imu", maxSize=50, blocking=False) if imu_on else None
        )
        self._verify_camera_output_resolution()
        pv_w, pv_h = self._preview_wh
        print(
            f"oak_pipeline=sensor_1200p isp_scale={OAK_ISP_SCALE_NUM}/{OAK_ISP_SCALE_DEN} "
            f"hw_jpeg={int(self._hw_jpeg)} hw_h264={int(self._hw_h264)} h264_seq={int(OAK_H264_SEQUENTIAL)} "
            f"h264_kf={OAK_H264_KEYFRAME_FREQUENCY} cam_q={OAK_CAM_QUEUE_MAX} "
            f"imagemanip={int(OAK_USE_IMAGEMANIP)} "
            f"gpio_fsync={int(OAK_GPIO_FSYNC)} device_fps={self.device_fps} "
            f"capture={cap_w}x{cap_h} preview={pv_w}x{pv_h} mjpeg_q={OAK_MJPEG_QUALITY} "
            f"h264_kbps={OAK_H264_BITRATE_KBPS}",
            flush=True,
        )

    def _verify_camera_output_resolution(self, *, timeout_s: float = 8.0) -> None:
        """Ensure capture streams match ego_spec (1280x800 ISP output)."""
        expected_h = int(OAK_DEFAULT_FRAME_HEIGHT)
        expected_w = int(OAK_DEFAULT_FRAME_WIDTH)
        deadline = time.monotonic() + timeout_s
        shapes: dict[str, tuple[int, int]] = {}
        min_jpeg_bytes = 8_000
        while time.monotonic() < deadline:
            for name, q in self._cam_queues.items():
                if name in shapes:
                    continue
                pkt = q.tryGet()
                if pkt is None:
                    continue
                if self._hw_jpeg or self._hw_h264:
                    blob = _jpeg_from_packet(pkt)
                    min_bytes = 64 if self._hw_h264 else min_jpeg_bytes
                    if blob is not None and len(blob) >= min_bytes:
                        shapes[name] = (expected_h, expected_w)
                    continue
                size = _packet_frame_size(pkt, hw_jpeg=False)
                if size is not None:
                    shapes[name] = size
            if len(shapes) >= len(self._cam_queues):
                break
            time.sleep(0.05)
        if len(shapes) < len(self._cam_queues):
            missing = sorted(set(self._cam_queues) - set(shapes))
            raise RuntimeError(
                f"OAK resolution check: no frames from {missing} within {timeout_s}s"
            )
        bad: list[str] = []
        for name, (h, w) in sorted(shapes.items()):
            if (h, w) != (expected_h, expected_w):
                bad.append(f"{name}={w}x{h} expected {expected_w}x{expected_h}")
        if bad:
            raise RuntimeError("OAK output resolution mismatch: " + "; ".join(bad))
        mode = "h264" if self._hw_h264 else ("mjpeg" if self._hw_jpeg else "bgr")
        print(
            f"oak_output_resolution ok mode={mode} 4x{expected_w}x{expected_h} "
            f"cams={sorted(shapes.keys())}",
            flush=True,
        )

    def _device_has_imu(self, device: Any) -> bool:
        try:
            imu_type = device.getConnectedIMU()
        except Exception:
            return False
        if not imu_type:
            return False
        return imu_type.strip().upper() not in ("", "NONE", "UNKNOWN")

    def _create_image_manip_resize(self, pipeline: Any, width: int, height: int) -> Any:
        dai = self._dai
        manip = pipeline.create(dai.node.ImageManip)
        manip.initialConfig.setResize(width, height)
        manip.setMaxOutputFrameSize(max(1, width * height * 3))
        return manip

    def _create_mjpeg_encoder(self, pipeline: Any) -> Any:
        dai = self._dai
        enc = pipeline.create(dai.node.VideoEncoder)
        enc.setDefaultProfilePreset(
            self.device_fps, dai.node.VideoEncoder.Properties.Profile.MJPEG
        )
        try:
            enc.setQuality(OAK_MJPEG_QUALITY)
        except Exception:
            pass
        return enc

    def _create_h264_encoder(self, pipeline: Any) -> Any:
        dai = self._dai
        enc = pipeline.create(dai.node.VideoEncoder)
        enc.setDefaultProfilePreset(
            self.device_fps, dai.node.VideoEncoder.Properties.Profile.H264_MAIN
        )
        try:
            enc.setBitrateKbps(OAK_H264_BITRATE_KBPS)
        except Exception:
            pass
        try:
            enc.setKeyframeFrequency(OAK_H264_KEYFRAME_FREQUENCY)
        except Exception:
            pass
        return enc

    def _create_pipeline(self, enable_imu: bool, board_rev: str) -> Any:
        dai = self._dai
        pipeline = dai.Pipeline()
        cap_w = int(OAK_DEFAULT_FRAME_WIDTH)
        cap_h = int(OAK_DEFAULT_FRAME_HEIGHT)
        pv_w, pv_h = self._preview_wh

        if enable_imu:
            imu = pipeline.create(dai.node.IMU)
            imu.enableIMUSensor(
                [dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW],
                self.imu_hz,
            )
            imu.setBatchReportThreshold(1)
            imu.setMaxBatchReports(10)
            imu_out = pipeline.create(dai.node.XLinkOut)
            imu_out.setStreamName("imu")
            imu.out.link(imu_out.input)

        for cam_name, cam_props in self._cam_list.items():
            xout = pipeline.create(dai.node.XLinkOut)
            xout.setStreamName(cam_name)

            if cam_props["color"]:
                cam = pipeline.create(dai.node.ColorCamera)
                res_key = cam_props.get("res", OAK_SENSOR_RES_KEY)
                cam.setResolution(COLOR_RES_OPTS.get(res_key, COLOR_RES_OPTS[OAK_SENSOR_RES_KEY]))
                isp_scale = cam_props.get("isp_scale", (OAK_ISP_SCALE_NUM, OAK_ISP_SCALE_DEN))
                cam.setIspScale(int(isp_scale[0]), int(isp_scale[1]))
                cam.setInterleaved(False)
                cam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)

                if self._hw_h264:
                    cam.setVideoSize(cap_w, cap_h)
                    enc_cap = self._create_h264_encoder(pipeline)
                    cam.video.link(enc_cap.input)
                    enc_cap.bitstream.link(xout.input)
                    if pv_w > 0 and pv_h > 0 and OAK_HW_PREVIEW_H264:
                        cam.setPreviewSize(pv_w, pv_h)
                        enc_pv = self._create_mjpeg_encoder(pipeline)
                        cam.preview.link(enc_pv.input)
                        xout_pv = pipeline.create(dai.node.XLinkOut)
                        xout_pv.setStreamName(f"{cam_name}_preview")
                        enc_pv.bitstream.link(xout_pv.input)
                elif self._hw_jpeg:
                    cam.setVideoSize(cap_w, cap_h)
                    enc_cap = self._create_mjpeg_encoder(pipeline)
                    cam.video.link(enc_cap.input)
                    enc_cap.bitstream.link(xout.input)
                    if pv_w > 0 and pv_h > 0 and OAK_HW_PREVIEW:
                        cam.setPreviewSize(pv_w, pv_h)
                        enc_pv = self._create_mjpeg_encoder(pipeline)
                        cam.preview.link(enc_pv.input)
                        xout_pv = pipeline.create(dai.node.XLinkOut)
                        xout_pv.setStreamName(f"{cam_name}_preview")
                        enc_pv.bitstream.link(xout_pv.input)
                elif OAK_USE_IMAGEMANIP:
                    manip_cap = self._create_image_manip_resize(pipeline, cap_w, cap_h)
                    cam.isp.link(manip_cap.inputImage)
                    manip_cap.out.link(xout.input)
                    if pv_w > 0 and pv_h > 0:
                        manip_pv = self._create_image_manip_resize(pipeline, pv_w, pv_h)
                        xout_pv = pipeline.create(dai.node.XLinkOut)
                        xout_pv.setStreamName(f"{cam_name}_preview")
                        cam.isp.link(manip_pv.inputImage)
                        manip_pv.out.link(xout_pv.input)
                else:
                    cam.isp.link(xout.input)
            else:
                cam = pipeline.create(dai.node.MonoCamera)
                cam.setResolution(MONO_RES_OPTS[cam_props["res"]])
                cam.out.link(xout.input)

            cam.setBoardSocket(CAM_SOCKET_OPTS[cam_name])
            cam.setFps(self.device_fps)

            if OAK_GPIO_FSYNC:
                if self._use_gpio_fsync:
                    cam.initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.INPUT)
                elif cam_name == PRIMARY_OAK_SOCKET:
                    cam.initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.OUTPUT)
                else:
                    cam.initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.INPUT)

        revision = parse_board_revision(board_rev)
        if OAK_GPIO_FSYNC and (self._use_gpio_fsync or revision >= 6):
            script = pipeline.create(dai.node.Script)
            script.setProcessor(dai.ProcessorType.LEON_CSS)
            script.setScript(FSYNC_GPIO_SCRIPT % float(self.device_fps))

        return pipeline

    def stop(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = None
        self._cam_queues = {}
        self._preview_queues = {}
        self._imu_queue = None

    def _drain_imu(self, buf: EpisodeBuffers) -> None:
        if self._imu_queue is None:
            return
        try:
            for imu_msg in self._imu_queue.tryGetAll():
                for pkt in imu_msg.packets:
                    accel = pkt.acceleroMeter
                    gyro = pkt.gyroscope
                    buf.accel_ts_ns.append(_device_ts_ns(accel.getTimestampDevice()))
                    buf.accel_xyz.append((float(accel.x), float(accel.y), float(accel.z)))
                    buf.gyro_ts_ns.append(_device_ts_ns(gyro.getTimestampDevice()))
                    buf.gyro_xyz.append((float(gyro.x), float(gyro.y), float(gyro.z)))
        except RuntimeError:
            pass

    @staticmethod
    def _trim_imu_buffer(buf: EpisodeBuffers, max_len: int = STRICT_IMU_BUFFER_MAX) -> None:
        """Keep a sliding IMU window so strict capture stays O(1) per frame."""
        n = len(buf.gyro_ts_ns)
        if n <= max_len:
            return
        drop = n - max_len
        del buf.gyro_ts_ns[:drop]
        del buf.gyro_xyz[:drop]
        del buf.accel_ts_ns[:drop]
        del buf.accel_xyz[:drop]

    def pop_pending_imu_raw(self) -> list[dict[str, Any]]:
        out = self._pending_imu_raw
        self._pending_imu_raw = []
        return out

    def flush_remaining_imu_raw(self) -> list[dict[str, Any]]:
        buf = self._strict_imu_buf
        if buf is not None:
            self._flush_imu_raw_from_buf(buf)
        return self.pop_pending_imu_raw()

    def _flush_imu_raw_from_buf(self, buf: EpisodeBuffers) -> None:
        from ego_capture_studio.capture.imu_raw_flush import collect_imu_raw_since

        batch, self._imu_flush_gyro_idx, self._imu_flush_accel_idx = collect_imu_raw_since(
            buf,
            gyro_from=self._imu_flush_gyro_idx,
            accel_from=self._imu_flush_accel_idx,
        )
        if batch:
            self._pending_imu_raw.extend(batch)

    def _drain_preview_queues(self) -> dict[str, bytes] | dict[str, np.ndarray]:
        if self._hw_jpeg:
            last_preview: dict[str, bytes] = {}
            for cam_name, queue in self._preview_queues.items():
                pkt = queue.tryGet()
                while pkt is not None:
                    jpeg = _jpeg_from_packet(pkt)
                    if jpeg is not None:
                        last_preview[cam_name] = jpeg
                    pkt = queue.tryGet()
            return last_preview
        last_preview_bgr: dict[str, np.ndarray] = {}
        for cam_name, queue in self._preview_queues.items():
            pkt = queue.tryGet()
            while pkt is not None:
                frame = _frame_from_packet(pkt)
                if frame is not None:
                    last_preview_bgr[cam_name] = frame
                pkt = queue.tryGet()
        return last_preview_bgr

    def record_episode(self, duration_s: float) -> EpisodeBuffers:
        if self._device is None:
            raise RuntimeError("Call connect() first")

        buf = EpisodeBuffers()
        probe_placeholder = np.zeros((2, 2, 3), dtype=np.uint8)
        last_seen: dict[str, bytes] | dict[str, np.ndarray] = {}
        t_end = time.monotonic() + float(duration_s)

        while time.monotonic() < t_end:
            self._drain_imu(buf)
            got_primary = False
            ts_ns: int | None = None

            for cam_name, queue in self._cam_queues.items():
                pkt = queue.tryGet()
                while pkt is not None:
                    if self._hw_jpeg or self._hw_h264:
                        jpeg = _jpeg_from_packet(pkt)
                        if jpeg is not None:
                            last_seen[cam_name] = jpeg
                    else:
                        frame = _frame_from_packet(pkt)
                        if frame is not None:
                            last_seen[cam_name] = frame
                    if cam_name == PRIMARY_OAK_SOCKET:
                        got_primary = True
                        ts_ns = _device_ts_ns(pkt.getTimestampDevice())
                    pkt = queue.tryGet()

            if not got_primary or ts_ns is None:
                continue
            if not self._cams_warmed:
                if not all(name in last_seen for name in self._cam_list):
                    continue
                self._cams_warmed = True

            for oak_name in self._cam_list:
                lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[oak_name]
                if self._hw_jpeg or self._hw_h264:
                    buf.camera_frames[lerobot_key].append(probe_placeholder)
                else:
                    buf.camera_frames[lerobot_key].append(last_seen[oak_name])
            buf.rgb_ts_ns.append(ts_ns)

        if not buf.rgb_ts_ns:
            return buf

        cameras_meta: dict[str, Any] = {}
        cap_h = int(OAK_DEFAULT_FRAME_HEIGHT)
        cap_w = int(OAK_DEFAULT_FRAME_WIDTH)
        shapes = {} if self._hw_h264 else buf.video_shapes()
        for oak_name in self._cam_list:
            lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[oak_name]
            if self._hw_h264:
                h, w = cap_h, cap_w
            elif lerobot_key not in shapes:
                continue
            else:
                h, w = shapes[lerobot_key]
            socket = CAM_SOCKET_OPTS[oak_name]
            intr = _read_camera_intrinsics(self._calib, socket, w, h)
            intr["oak_socket"] = oak_name
            cameras_meta[lerobot_key] = intr

        buf.camera_intrinsics = {
            "primary_lerobot_key": OAK_SOCKET_TO_LEROBOT_VIDEO[PRIMARY_OAK_SOCKET],
            "primary_oak_socket": PRIMARY_OAK_SOCKET,
            "cameras": cameras_meta,
        }
        return buf

    def record_episode_probe(
        self,
        *,
        min_s: float = 0.8,
        max_s: float = 3.0,
    ) -> tuple[EpisodeBuffers, float]:
        """Adaptive warmup probe: exit once all cameras warmed after min_s, cap at max_s."""
        if self._device is None:
            raise RuntimeError("Call connect() first")

        t_start = time.monotonic()
        t_min_done = t_start + float(min_s)
        t_end = t_start + float(max_s)

        buf = EpisodeBuffers()
        probe_placeholder = np.zeros((2, 2, 3), dtype=np.uint8)
        last_seen: dict[str, bytes] | dict[str, np.ndarray] = {}

        while time.monotonic() < t_end:
            self._drain_imu(buf)
            got_primary = False
            ts_ns: int | None = None

            for cam_name, queue in self._cam_queues.items():
                pkt = queue.tryGet()
                while pkt is not None:
                    if self._hw_jpeg or self._hw_h264:
                        jpeg = _jpeg_from_packet(pkt)
                        if jpeg is not None:
                            last_seen[cam_name] = jpeg
                    else:
                        frame = _frame_from_packet(pkt)
                        if frame is not None:
                            last_seen[cam_name] = frame
                    if cam_name == PRIMARY_OAK_SOCKET:
                        got_primary = True
                        ts_ns = _device_ts_ns(pkt.getTimestampDevice())
                    pkt = queue.tryGet()

            if not got_primary or ts_ns is None:
                continue
            if not self._cams_warmed:
                if not all(name in last_seen for name in self._cam_list):
                    continue
                self._cams_warmed = True

            for oak_name in self._cam_list:
                lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[oak_name]
                if self._hw_jpeg or self._hw_h264:
                    buf.camera_frames[lerobot_key].append(probe_placeholder)
                else:
                    buf.camera_frames[lerobot_key].append(last_seen[oak_name])
            buf.rgb_ts_ns.append(ts_ns)

            if (
                self._cams_warmed
                and buf.rgb_ts_ns
                and time.monotonic() >= t_min_done
            ):
                break

        elapsed = time.monotonic() - t_start
        if not buf.rgb_ts_ns:
            return buf, elapsed

        cameras_meta: dict[str, Any] = {}
        cap_h = int(OAK_DEFAULT_FRAME_HEIGHT)
        cap_w = int(OAK_DEFAULT_FRAME_WIDTH)
        shapes = {} if self._hw_h264 else buf.video_shapes()
        for oak_name in self._cam_list:
            lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[oak_name]
            if self._hw_h264:
                h, w = cap_h, cap_w
            elif lerobot_key not in shapes:
                continue
            else:
                h, w = shapes[lerobot_key]
            socket = CAM_SOCKET_OPTS[oak_name]
            intr = _read_camera_intrinsics(self._calib, socket, w, h)
            intr["oak_socket"] = oak_name
            cameras_meta[lerobot_key] = intr

        buf.camera_intrinsics = {
            "primary_lerobot_key": OAK_SOCKET_TO_LEROBOT_VIDEO[PRIMARY_OAK_SOCKET],
            "primary_oak_socket": PRIMARY_OAK_SOCKET,
            "cameras": cameras_meta,
        }
        return buf, elapsed

    def iter_synced_frames(self, duration_s: float):
        """Yield (timestamp_ns, capture, preview, imu6).

        capture/preview are dict[str, bytes] (hw MJPEG) or dict[str, ndarray] BGR (software).
        """
        if self._device is None:
            raise RuntimeError("Call connect() first")

        from ego_capture_studio.capture.buffers import EpisodeBuffers
        from ego_capture_studio.capture.camera_map import OAK_SOCKET_TO_LEROBOT_VIDEO, PRIMARY_OAK_SOCKET
        from ego_capture_studio.capture.imu_align import imu6_at_timestamp
        from ego_capture_studio.capture.lerobot_episode import _buffers_to_numpy

        buf = EpisodeBuffers()
        last_seen: dict[str, bytes] | dict[str, np.ndarray] = {}
        last_preview_oak: dict[str, bytes] | dict[str, np.ndarray] = {}
        t_end = time.monotonic() + float(duration_s)

        while time.monotonic() < t_end:
            self._drain_imu(buf)
            got_primary = False
            ts_ns: int | None = None

            for cam_name, queue in self._cam_queues.items():
                pkt = queue.tryGet()
                while pkt is not None:
                    if self._hw_jpeg or self._hw_h264:
                        jpeg = _jpeg_from_packet(pkt)
                        if jpeg is not None:
                            last_seen[cam_name] = jpeg
                    else:
                        frame = _frame_from_packet(pkt)
                        if frame is not None:
                            last_seen[cam_name] = frame
                    if cam_name == PRIMARY_OAK_SOCKET:
                        got_primary = True
                        ts_ns = _device_ts_ns(pkt.getTimestampDevice())
                    pkt = queue.tryGet()

            preview_drain = self._drain_preview_queues()
            if preview_drain:
                last_preview_oak.update(preview_drain)

            if not got_primary or ts_ns is None:
                continue
            if not self._cams_warmed:
                if not all(name in last_seen for name in self._cam_list):
                    continue
                self._cams_warmed = True

            self._primary_tick += 1
            for oak in self._cam_list:
                if oak in last_seen:
                    self._last_capture[oak] = last_seen[oak]
            capture_out = {
                OAK_SOCKET_TO_LEROBOT_VIDEO[oak]: self._last_capture[oak]
                for oak in self._cam_list
                if oak in self._last_capture
            }
            if len(capture_out) < len(self._cam_list):
                continue
            preview_out = {
                OAK_SOCKET_TO_LEROBOT_VIDEO[oak]: last_preview_oak[oak]
                for oak in self._cam_list
                if oak in last_preview_oak
            }
            g_ts, g, a_ts, a = _buffers_to_numpy(buf)
            imu6 = imu6_at_timestamp(g_ts, g, a_ts, a, ts_ns)
            yield int(ts_ns), capture_out, preview_out, imu6

    def iter_strict_sync_frames(
        self,
        duration_s: float,
        *,
        interval_ms: int | None = None,
        imu_interpolate: bool | None = None,
        grid_epoch_ns: int = 0,
        shutdown_check: Callable[[], bool] | None = None,
    ):
        """Yield strict grid frames: (t_grid_ns, capture, preview, imu6, camera_ts_offset_ns).

        OAK device_fps (typically 30) feeds per-camera ring buffers; a wall-clock gate emits
        exactly one frame per interval_ms. timestamp_ns is the uniform grid (not raw device ts).
        """
        if self._device is None:
            raise RuntimeError("Call connect() first")

        from ego_capture_studio.capture.buffers import EpisodeBuffers
        from ego_capture_studio.capture.camera_map import OAK_SOCKET_TO_LEROBOT_VIDEO, PRIMARY_OAK_SOCKET
        from ego_capture_studio.capture.imu_align import imu6_at_timestamp
        from ego_capture_studio.capture.lerobot_episode import _buffers_to_numpy

        ms = int(interval_ms if interval_ms is not None else (ego_frame_interval_ns() // 1_000_000))
        interval_ns = int(interval_ms) * 1_000_000 if interval_ms is not None else ego_frame_interval_ns()
        interval_s = interval_ns / 1e9
        use_imu_interp = EGO_IMU_INTERPOLATE if imu_interpolate is None else bool(imu_interpolate)

        self._imu_flush_gyro_idx = 0
        self._imu_flush_accel_idx = 0
        self._pending_imu_raw = []

        buf = EpisodeBuffers()
        self._strict_imu_buf = buf
        cam_rings: dict[str, deque[_CamRingSample]] = {
            oak: deque(maxlen=self._strict_ring_len(oak)) for oak in self._cam_list
        }
        last_preview_oak: dict[str, bytes] | dict[str, np.ndarray] = {}
        global_idx = 0
        t_end = time.monotonic() + float(duration_s)
        start_mono: float | None = None
        epoch_ns: int | None = None
        last_primary_ts_ns: int | None = None
        last_emit_ts_ns: int | None = self._strict_last_emit_ts_ns
        last_yield_global_idx: int | None = None
        pending_reanchor = False
        reanchor_warmup_ticks = 0
        sync_miss_streak = 0
        self._h264_stale_drops = 0

        def _strict_sync_miss() -> None:
            nonlocal sync_miss_streak, global_idx
            sync_miss_streak += 1
            if sync_miss_streak >= STRICT_SYNC_MISS_MAX:
                global_idx += 1
                sync_miss_streak = 0
            time.sleep(0.0005)

        while time.monotonic() < t_end:
            if shutdown_check and shutdown_check():
                return
            self._drain_imu(buf)
            self._flush_imu_raw_from_buf(buf)
            self._trim_imu_buffer(buf)
            self._imu_flush_gyro_idx = len(buf.gyro_ts_ns)
            self._imu_flush_accel_idx = len(buf.accel_ts_ns)
            for cam_name, queue in self._cam_queues.items():
                pkt = queue.tryGet()
                while pkt is not None:
                    ts = _device_ts_ns(pkt.getTimestampDevice())
                    if cam_name == PRIMARY_OAK_SOCKET and cam_rings[cam_name]:
                        prev_ts = int(cam_rings[cam_name][-1].ts_ns)
                        if abs(int(ts) - prev_ts) > STRICT_TS_JUMP_NS:
                            pending_reanchor = True
                    if self._hw_jpeg or self._hw_h264:
                        payload = _jpeg_from_packet(pkt)
                    else:
                        payload = _frame_from_packet(pkt)
                    if payload is not None:
                        cam_rings[cam_name].append(_CamRingSample(ts, payload))
                    pkt = queue.tryGet()

            preview_drain = self._drain_preview_queues()
            if preview_drain:
                last_preview_oak.update(preview_drain)

            if not all(cam_rings[oak] for oak in self._cam_list):
                time.sleep(0.0005)
                continue

            if pending_reanchor and start_mono is not None:
                new_epoch = self._reanchor_strict_grid(
                    cam_rings, interval_ns=interval_ns, reason="primary_packet_jump"
                )
                if new_epoch is not None:
                    epoch_ns = new_epoch
                    global_idx = 0
                    start_mono = time.monotonic()
                    last_primary_ts_ns = None
                    last_yield_global_idx = None
                    reanchor_warmup_ticks = STRICT_REANCHOR_WARMUP_TICKS
                pending_reanchor = False
                continue

            if start_mono is None:
                start_mono = time.monotonic()
                if int(grid_epoch_ns) > 0:
                    epoch_ns = int(grid_epoch_ns)
                else:
                    primary_ring = cam_rings.get(PRIMARY_OAK_SOCKET)
                    if not primary_ring:
                        time.sleep(0.0005)
                        continue
                    epoch_ns = self._align_epoch_to_device(
                        int(primary_ring[-1].ts_ns), interval_ns
                    )
                self._strict_grid_epoch_ns = int(epoch_ns)

            assert start_mono is not None and epoch_ns is not None
            target_mono = start_mono + global_idx * interval_s
            now = time.monotonic()
            if now < target_mono:
                time.sleep(min(0.002, target_mono - now))
                continue

            t_grid_ns = int(epoch_ns) + global_idx * interval_ns
            primary_sample = self._ring_sample_for_yield(
                cam_rings[PRIMARY_OAK_SOCKET], PRIMARY_OAK_SOCKET, t_grid_ns
            )
            if primary_sample is None:
                _strict_sync_miss()
                continue
            primary_ts_ns = int(primary_sample.ts_ns)
            capture_out: dict[str, bytes] | dict[str, np.ndarray] = {}
            offsets: dict[str, int] = {}
            for oak in self._cam_list:
                lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[oak]
                if oak == PRIMARY_OAK_SOCKET:
                    sample = primary_sample
                else:
                    sample = self._ring_sample_for_yield(
                        cam_rings[oak], oak, primary_ts_ns
                    )
                if sample is None:
                    break
                offsets[lerobot_key] = int(sample.ts_ns) - primary_ts_ns
                capture_out[lerobot_key] = sample.payload
            if len(capture_out) < len(self._cam_list):
                _strict_sync_miss()
                continue

            yield_block = self._strict_exceeds_yield_limits(offsets)
            if yield_block is not None:
                _strict_sync_miss()
                continue

            grid_ticks = (
                global_idx - last_yield_global_idx
                if last_yield_global_idx is not None
                else 1
            )
            reanchor_reason = self._strict_needs_reanchor(
                primary_ts_ns=primary_ts_ns,
                last_primary_ts_ns=last_primary_ts_ns,
                grid_ticks_since_last_yield=grid_ticks,
                interval_ns=interval_ns,
            )
            if reanchor_reason is not None:
                new_epoch = self._reanchor_strict_grid(
                    cam_rings, interval_ns=interval_ns, reason=reanchor_reason
                )
                if new_epoch is not None:
                    epoch_ns = new_epoch
                    global_idx = 0
                    start_mono = time.monotonic()
                    last_primary_ts_ns = None
                    last_yield_global_idx = None
                    reanchor_warmup_ticks = STRICT_REANCHOR_WARMUP_TICKS
                continue

            preview_out = {
                OAK_SOCKET_TO_LEROBOT_VIDEO[oak]: last_preview_oak[oak]
                for oak in self._cam_list
                if oak in last_preview_oak
            }
            if last_emit_ts_ns is not None:
                t_emit_ns = int(last_emit_ts_ns) + interval_ns
            else:
                t_emit_ns = int(t_grid_ns)
            g_ts, g, a_ts, a = _buffers_to_numpy(buf)
            imu6 = imu6_at_timestamp(
                g_ts, g, a_ts, a, t_emit_ns, interpolate=use_imu_interp
            )
            last_primary_ts_ns = int(primary_ts_ns)
            last_emit_ts_ns = int(t_emit_ns)
            self._strict_last_emit_ts_ns = last_emit_ts_ns
            last_yield_global_idx = int(global_idx)
            sync_miss_streak = 0
            if reanchor_warmup_ticks > 0:
                reanchor_warmup_ticks -= 1
            global_idx += 1
            yield int(t_emit_ns), capture_out, preview_out, imu6, offsets

    def __enter__(self) -> "Oak4pEgoRecorder":
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
