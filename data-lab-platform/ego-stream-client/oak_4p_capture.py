"""OAK-4P-New / OAK-FFC-4P four-camera capture with optional IMU (DepthAI)."""

from __future__ import annotations

import re
import time
from typing import Any

import numpy as np

from ego_capture_studio.capture.buffers import EpisodeBuffers
from ego_capture_studio.capture.camera_map import (
    ALL_OAK_SOCKETS,
    OAK_SOCKET_TO_LEROBOT_VIDEO,
    PRIMARY_OAK_SOCKET,
)
from ego_capture_studio.capture.ego_spec import (
    OAK_CAPTURE_FPS,
    OAK_CAPTURE_IMU_HZ,
    OAK_CAPTURE_RES_KEY,
    OAK_DEFAULT_FRAME_HEIGHT,
    OAK_DEFAULT_FRAME_WIDTH,
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
    name = sensor_name.upper()
    type_names = {t.name for t in supported_types}
    is_color = "COLOR" in type_names or "RGB" in type_names
    if "OV9282" in name or "OV9281" in name:
        res = OAK_CAPTURE_RES_KEY if OAK_CAPTURE_RES_KEY in MONO_RES_OPTS else "800"
        return {"color": False, "res": res}
    if "AR0234" in name or "AR023" in name:
        # AR0234 native mode is 1200_P only; 800P via board ISP scale (1280x800).
        if OAK_CAPTURE_RES_KEY == "800":
            return {"color": True, "res": "1200", "isp_scale": (2, 3)}
        res = OAK_CAPTURE_RES_KEY if OAK_CAPTURE_RES_KEY in COLOR_RES_OPTS else "1200"
        return {"color": True, "res": res}
    return {"color": is_color, "res": OAK_CAPTURE_RES_KEY if is_color else "800"}


def _device_ts_ns(ts_device: Any) -> int:
    return int(float(ts_device.total_seconds()) * 1e9)


def _bgr_to_rgb(bgr: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(bgr[:, :, ::-1], dtype=np.uint8)


def _read_camera_intrinsics(calib: Any, socket: Any, width: int, height: int) -> dict[str, Any]:
    try:
        mat = np.array(calib.getCameraIntrinsics(socket, width, height))
        fx, fy = float(mat[0, 0]), float(mat[1, 1])
        ppx, ppy = float(mat[0, 2]), float(mat[1, 2])
    except Exception:
        fx = fy = float(max(width, height))
        ppx, ppy = width / 2.0, height / 2.0
    return {
        "width": int(width),
        "height": int(height),
        "fx": fx,
        "fy": fy,
        "ppx": ppx,
        "ppy": ppy,
        "coeffs": [0.0, 0.0, 0.0, 0.0, 0.0],
        "distortion_model_name": "oak_depthai",
    }


class Oak4pEgoRecorder:
    """Record four synchronized OAK cameras + optional IMU into EpisodeBuffers."""

    def __init__(
        self,
        *,
        fps: int = OAK_CAPTURE_FPS,
        imu_hz: int = OAK_CAPTURE_IMU_HZ,
        enable_imu: bool = True,
        force_imu: bool = False,
    ) -> None:
        self.fps = int(fps)
        self.imu_hz = int(imu_hz)
        self.enable_imu = enable_imu
        self.force_imu = force_imu
        self._dai: Any = None
        self._device: Any = None
        self._cam_list: dict[str, dict[str, Any]] = {}
        self._cam_queues: dict[str, Any] = {}
        self._imu_queue: Any = None
        self._use_gpio_fsync = False
        self._calib: Any = None

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
        self._calib = device.readCalibration2()
        eeprom = self._calib.getEepromData()
        board_rev = eeprom.boardRev or ""
        revision = parse_board_revision(board_rev)
        self._use_gpio_fsync = revision >= 6 or "FFC-4P" in (eeprom.productName or "")

        cam_list: dict[str, dict[str, Any]] = {
            name: {"color": True, "res": OAK_CAPTURE_RES_KEY} for name in ALL_OAK_SOCKETS
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

        has_imu_hw = self._device_has_imu(device)
        imu_on = self.enable_imu and (self.force_imu or has_imu_hw)
        if self.force_imu and not has_imu_hw:
            imu_on = True

        pipeline = self._create_pipeline(imu_on, board_rev)
        pipeline.setXLinkChunkSize(0)
        device.startPipeline(pipeline)

        self._cam_queues = {
            name: device.getOutputQueue(name=name, maxSize=8, blocking=False)
            for name in self._cam_list
        }
        self._imu_queue = (
            device.getOutputQueue("imu", maxSize=50, blocking=False) if imu_on else None
        )
        self._verify_camera_output_resolution()

    def _verify_camera_output_resolution(self, *, timeout_s: float = 8.0) -> None:
        """Ensure all cameras output 1280x800 (800P ISP) before streaming."""
        expected_h = int(OAK_DEFAULT_FRAME_HEIGHT)
        expected_w = int(OAK_DEFAULT_FRAME_WIDTH)
        deadline = time.monotonic() + timeout_s
        shapes: dict[str, tuple[int, int]] = {}
        while time.monotonic() < deadline:
            for name, q in self._cam_queues.items():
                if name in shapes:
                    continue
                pkt = q.tryGet()
                if pkt is None:
                    continue
                frame = pkt.getCvFrame()
                if frame is None or frame.size == 0:
                    continue
                h, w = int(frame.shape[0]), int(frame.shape[1])
                shapes[name] = (h, w)
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
        print(
            f"oak_output_resolution ok 4x{expected_w}x{expected_h} "
            f"cams={sorted(shapes.keys())}"
        )

    def _device_has_imu(self, device: Any) -> bool:
        try:
            imu_type = device.getConnectedIMU()
        except Exception:
            return False
        if not imu_type:
            return False
        return imu_type.strip().upper() not in ("", "NONE", "UNKNOWN")

    def _create_pipeline(self, enable_imu: bool, board_rev: str) -> Any:
        dai = self._dai
        pipeline = dai.Pipeline()

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
                cam.setResolution(COLOR_RES_OPTS[cam_props["res"]])
                isp_scale = cam_props.get("isp_scale")
                if isp_scale:
                    cam.setIspScale(int(isp_scale[0]), int(isp_scale[1]))
                cam.setInterleaved(False)
                cam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
                cam.isp.link(xout.input)
            else:
                cam = pipeline.create(dai.node.MonoCamera)
                cam.setResolution(MONO_RES_OPTS[cam_props["res"]])
                cam.out.link(xout.input)

            cam.setBoardSocket(CAM_SOCKET_OPTS[cam_name])
            cam.setFps(self.fps)

            if self._use_gpio_fsync:
                cam.initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.INPUT)
            elif cam_name == PRIMARY_OAK_SOCKET:
                cam.initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.OUTPUT)
            else:
                cam.initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.INPUT)

        revision = parse_board_revision(board_rev)
        if self._use_gpio_fsync or revision >= 6:
            script = pipeline.create(dai.node.Script)
            script.setProcessor(dai.ProcessorType.LEON_CSS)
            script.setScript(FSYNC_GPIO_SCRIPT % float(self.fps))

        return pipeline

    def stop(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = None
        self._cam_queues = {}
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

    def record_episode(self, duration_s: float) -> EpisodeBuffers:
        if self._device is None:
            raise RuntimeError("Call connect() first")

        buf = EpisodeBuffers()
        last_seen: dict[str, np.ndarray] = {}
        t_end = time.monotonic() + float(duration_s)

        while time.monotonic() < t_end:
            self._drain_imu(buf)
            got_primary = False
            ts_ns: int | None = None

            for cam_name, queue in self._cam_queues.items():
                pkt = queue.tryGet()
                while pkt is not None:
                    last_seen[cam_name] = pkt.getCvFrame()
                    if cam_name == PRIMARY_OAK_SOCKET:
                        got_primary = True
                        ts_ns = _device_ts_ns(pkt.getTimestampDevice())
                    pkt = queue.tryGet()

            if not got_primary or ts_ns is None:
                continue
            if not all(name in last_seen for name in self._cam_list):
                continue

            for oak_name in self._cam_list:
                lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[oak_name]
                buf.camera_frames[lerobot_key].append(_bgr_to_rgb(last_seen[oak_name]))
            buf.rgb_ts_ns.append(ts_ns)

        if not buf.rgb_ts_ns:
            return buf

        shapes = buf.video_shapes()
        cameras_meta: dict[str, Any] = {}
        for oak_name in self._cam_list:
            lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[oak_name]
            if lerobot_key not in shapes:
                continue
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



    def iter_synced_frames(self, duration_s: float):
        """Yield per synced quad-frame: (timestamp_ns, lerobot_key->rgb, imu6)."""
        if self._device is None:
            raise RuntimeError("Call connect() first")

        from ego_capture_studio.capture.buffers import EpisodeBuffers
        from ego_capture_studio.capture.camera_map import OAK_SOCKET_TO_LEROBOT_VIDEO, PRIMARY_OAK_SOCKET
        from ego_capture_studio.capture.imu_align import imu6_at_timestamp
        from ego_capture_studio.capture.lerobot_episode import _buffers_to_numpy

        buf = EpisodeBuffers()
        last_seen: dict[str, object] = {}
        t_end = time.monotonic() + float(duration_s)

        while time.monotonic() < t_end:
            self._drain_imu(buf)
            got_primary = False
            ts_ns: int | None = None

            for cam_name, queue in self._cam_queues.items():
                pkt = queue.tryGet()
                while pkt is not None:
                    last_seen[cam_name] = pkt.getCvFrame()
                    if cam_name == PRIMARY_OAK_SOCKET:
                        got_primary = True
                        ts_ns = _device_ts_ns(pkt.getTimestampDevice())
                    pkt = queue.tryGet()

            if not got_primary or ts_ns is None:
                continue
            if not all(name in last_seen for name in self._cam_list):
                continue

            frames = {
                OAK_SOCKET_TO_LEROBOT_VIDEO[oak]: _bgr_to_rgb(last_seen[oak])
                for oak in self._cam_list
            }
            g_ts, g, a_ts, a = _buffers_to_numpy(buf)
            imu6 = imu6_at_timestamp(g_ts, g, a_ts, a, ts_ns)
            yield int(ts_ns), frames, imu6

    def __enter__(self) -> "Oak4pEgoRecorder":
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
