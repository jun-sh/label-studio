#!/usr/bin/env python3
"""Luxonis-style OAK triplet bench: USB probe, 4-cam ISP drain, ego-like H.264+IMU.

Run on 130 with capture services stopped. Emits machine-readable lines:
  LUXONIS_BENCH phase=... key=value

No disk writes; host does not decode frames (packet drain only).
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict, deque

import depthai as dai

CAM_LIST = ("CAM_A", "CAM_B", "CAM_C", "CAM_D")
COLOR_RES = dai.ColorCameraProperties.SensorResolution.THE_1200_P
SOCKET = {
    "CAM_A": dai.CameraBoardSocket.CAM_A,
    "CAM_B": dai.CameraBoardSocket.CAM_B,
    "CAM_C": dai.CameraBoardSocket.CAM_C,
    "CAM_D": dai.CameraBoardSocket.CAM_D,
}

CAP_W = int(os.environ.get("OAK_DEFAULT_FRAME_WIDTH", "1280"))
CAP_H = int(os.environ.get("OAK_DEFAULT_FRAME_HEIGHT", "800"))
ISP_NUM = int(os.environ.get("OAK_ISP_SCALE_NUM", "2"))
ISP_DEN = int(os.environ.get("OAK_ISP_SCALE_DEN", "3"))
TARGET_FPS = int(os.environ.get("OAK_DEVICE_FPS", "30"))
IMU_HZ = int(os.environ.get("EGO_CAPTURE_IMU_HZ", "200"))
BITRATE_KBPS = int(os.environ.get("OAK_H264_BITRATE_KBPS", "3000"))
QUEUE_MAX = max(4, int(os.environ.get("OAK_CAM_QUEUE_MAX", "128")))
DURATION_S = float(os.environ.get("LUXONIS_BENCH_DURATION_S", "15"))
PRIMARY_SOCKET = "CAM_A"
OAK_GPIO_FSYNC = os.environ.get("OAK_GPIO_FSYNC", "0").strip().lower() in ("1", "true", "yes")

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


def _parse_board_revision(board_rev: str) -> int:
    if len(board_rev) >= 2 and board_rev[0] == "R":
        try:
            return int(board_rev[1])
        except ValueError:
            pass
    return -1


def _log(phase: str, **kwargs: object) -> None:
    parts = [f"LUXONIS_BENCH phase={phase}"]
    for key, val in kwargs.items():
        parts.append(f"{key}={val}")
    print(" ".join(parts), flush=True)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct))
    return float(ordered[max(0, min(idx, len(ordered) - 1))])


class InterArrivalStats:
    def __init__(self) -> None:
        self._last_ts: dict[str, int] = {}
        self._intervals_ms: dict[str, list[float]] = defaultdict(list)
        self._packet_count: dict[str, int] = defaultdict(int)
        self._byte_count: dict[str, int] = defaultdict(int)

    def note(self, stream: str, ts_ns: int, nbytes: int = 0) -> None:
        self._packet_count[stream] += 1
        self._byte_count[stream] += int(nbytes)
        ts = int(ts_ns)
        prev = self._last_ts.get(stream)
        if prev is not None and ts > prev:
            dt_ms = (ts - prev) / 1e6
            if 5.0 <= dt_ms <= 200.0:
                self._intervals_ms[stream].append(dt_ms)
        self._last_ts[stream] = ts

    def report(self, phase: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for stream in sorted(self._packet_count):
            count = self._packet_count[stream]
            bytes_total = self._byte_count[stream]
            vals = self._intervals_ms.get(stream, [])
            p50 = _percentile(vals, 0.50)
            p99 = _percentile(vals, 0.99)
            eff_hz = 1000.0 / p50 if p50 > 0 else 0.0
            wall_hz = count / DURATION_S if DURATION_S > 0 else 0.0
            out[f"{stream}_packets"] = float(count)
            out[f"{stream}_mbps"] = (bytes_total * 8 / 1e6) / DURATION_S if DURATION_S > 0 else 0.0
            out[f"{stream}_p50_ms"] = p50
            out[f"{stream}_p99_ms"] = p99
            out[f"{stream}_eff_hz"] = eff_hz
            out[f"{stream}_wall_hz"] = wall_hz
            _log(
                phase,
                stream=stream,
                packets=count,
                wall_hz=f"{wall_hz:.2f}",
                p50_ms=f"{p50:.2f}",
                p99_ms=f"{p99:.2f}",
                eff_hz=f"{eff_hz:.2f}",
                mbps=f"{out[f'{stream}_mbps']:.2f}",
            )
        cam_eff = [out[k] for k in out if k.endswith("_eff_hz") and k.startswith("CAM_")]
        if cam_eff:
            out["min_cam_eff_hz"] = min(cam_eff)
            out["avg_cam_eff_hz"] = sum(cam_eff) / len(cam_eff)
            _log(phase, summary="cams", min_eff_hz=f"{out['min_cam_eff_hz']:.2f}", avg_eff_hz=f"{out['avg_cam_eff_hz']:.2f}")
        return out


def _run_lsusb() -> None:
    _log("usb", step="lsusb_tree")
    try:
        tree = subprocess.run(["lsusb", "-t"], capture_output=True, text=True, timeout=10, check=False)
        for line in (tree.stdout or "").splitlines():
            if "03e7" in line.lower() or "Luxonis" in line or "DepthAI" in line or "480M" in line or "5000M" in line or "10000M" in line:
                print(line, flush=True)
        if not (tree.stdout or "").strip():
            print((tree.stdout or tree.stderr or "").strip(), flush=True)
    except Exception as exc:
        _log("usb", lsusb_error=str(exc))
    try:
        devices = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=10, check=False)
        for line in (devices.stdout or "").splitlines():
            if "03e7" in line:
                _log("usb", lsusb_device=line.strip())
    except Exception as exc:
        _log("usb", lsusb_error=str(exc))


def phase_usb() -> None:
    _run_lsusb()
    devs = dai.Device.getAllAvailableDevices()
    if not devs:
        _log("usb", error="no_device")
        sys.exit(1)
    for idx, info in enumerate(devs):
        _log("usb", index=idx, mxid=info.mxid, state=str(info.state))
    with dai.Device() as device:
        _log("usb", depthai_version=dai.__version__)
        try:
            _log("usb", usb_speed=str(device.getUsbSpeed()))
        except Exception as exc:
            _log("usb", usb_speed_error=str(exc))
        try:
            cal = device.readCalibration()
            eeprom = cal.getEepromData()
            _log("usb", product_name=getattr(eeprom, "productName", "") or "")
            _log("usb", board_name=getattr(eeprom, "boardName", "") or "")
        except Exception:
            pass
        try:
            cameras = device.getConnectedCameras()
            _log("usb", connected_cameras=len(cameras))
        except Exception:
            pass


def _isp_pipeline() -> dai.Pipeline:
    pipeline = dai.Pipeline()
    pipeline.setXLinkChunkSize(0)
    for cam_name in CAM_LIST:
        xout = pipeline.create(dai.node.XLinkOut)
        xout.setStreamName(cam_name)
        cam = pipeline.create(dai.node.ColorCamera)
        cam.setResolution(COLOR_RES)
        cam.setIspScale(ISP_NUM, ISP_DEN)
        cam.setInterleaved(False)
        cam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
        cam.setBoardSocket(SOCKET[cam_name])
        cam.setFps(TARGET_FPS)
        cam.isp.link(xout.input)
    return pipeline


def _h264_manip(pipeline: dai.Pipeline, width: int, height: int) -> dai.node.ImageManip:
    manip = pipeline.create(dai.node.ImageManip)
    manip.initialConfig.setResize(int(width), int(height))
    try:
        manip.initialConfig.setFrameType(dai.ImgFrame.Type.NV12)
    except Exception:
        try:
            manip.initialConfig.setFrameType(dai.RawImgFrame.Type.NV12)
        except Exception:
            pass
    manip.setMaxOutputFrameSize(max(1, int(width) * int(height) * 3 // 2))
    return manip


def _h264_pipeline(board_rev: str = "") -> dai.Pipeline:
    pipeline = dai.Pipeline()
    pipeline.setXLinkChunkSize(0)
    revision = _parse_board_revision(board_rev)
    imu = pipeline.create(dai.node.IMU)
    imu.enableIMUSensor(
        [dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW],
        IMU_HZ,
    )
    imu.setBatchReportThreshold(1)
    imu.setMaxBatchReports(10)
    imu_out = pipeline.create(dai.node.XLinkOut)
    imu_out.setStreamName("imu")
    imu.out.link(imu_out.input)

    for cam_name in CAM_LIST:
        xout = pipeline.create(dai.node.XLinkOut)
        xout.setStreamName(cam_name)
        cam = pipeline.create(dai.node.ColorCamera)
        cam.setResolution(COLOR_RES)
        cam.setIspScale(ISP_NUM, ISP_DEN)
        cam.setInterleaved(False)
        cam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
        cam.setBoardSocket(SOCKET[cam_name])
        cam.setFps(TARGET_FPS)
        if OAK_GPIO_FSYNC:
            if cam_name == PRIMARY_SOCKET:
                cam.initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.OUTPUT)
            else:
                cam.initialControl.setFrameSyncMode(dai.CameraControl.FrameSyncMode.INPUT)
        manip = _h264_manip(pipeline, CAP_W, CAP_H)
        enc = pipeline.create(dai.node.VideoEncoder)
        enc.setDefaultProfilePreset(TARGET_FPS, dai.node.VideoEncoder.Properties.Profile.H264_MAIN)
        try:
            enc.setBitrateKbps(BITRATE_KBPS)
        except Exception:
            pass
        try:
            enc.setKeyframeFrequency(max(1, TARGET_FPS))
        except Exception:
            pass
        cam.isp.link(manip.inputImage)
        manip.out.link(enc.input)
        enc.bitstream.link(xout.input)

    if OAK_GPIO_FSYNC and revision >= 6:
        script = pipeline.create(dai.node.Script)
        script.setProcessor(dai.ProcessorType.LEON_CSS)
        script.setScript(FSYNC_GPIO_SCRIPT % float(TARGET_FPS))
    return pipeline


def _drain_loop(
    device: dai.Device,
    pipeline: dai.Pipeline,
    *,
    phase: str,
    stream_names: list[str],
    include_imu: bool,
) -> dict[str, float]:
    device.startPipeline(pipeline)
    queues = {name: device.getOutputQueue(name, maxSize=QUEUE_MAX, blocking=False) for name in stream_names}
    imu_q = device.getOutputQueue("imu", maxSize=QUEUE_MAX, blocking=False) if include_imu else None
    stats = InterArrivalStats()
    imu_packets = 0
    t0 = time.monotonic()
    t_end = t0 + DURATION_S
    while time.monotonic() < t_end and not device.isClosed():
        for name in stream_names:
            q = queues[name]
            pkt = q.tryGet()
            while pkt is not None:
                try:
                    ts = int(pkt.getTimestampDevice().total_seconds() * 1e9)
                except Exception:
                    ts = int(time.time() * 1e9)
                nbytes = 0
                try:
                    data = pkt.getData()
                    nbytes = len(data) if data is not None else 0
                except Exception:
                    pass
                stats.note(name, ts, nbytes)
                pkt = q.tryGet()
        if imu_q is not None:
            pkt = imu_q.tryGet()
            while pkt is not None:
                imu_packets += 1
                pkt = imu_q.tryGet()
    wall = max(0.001, time.monotonic() - t0)
    if include_imu:
        _log(phase, imu_packets=imu_packets, imu_wall_hz=f"{imu_packets / wall:.1f}", imu_target_hz=IMU_HZ)
    _log(
        phase,
        duration_s=f"{wall:.1f}",
        target_fps=TARGET_FPS,
        resolution=f"{CAP_W}x{CAP_H}",
        isp_scale=f"{ISP_NUM}/{ISP_DEN}",
        queue_max=QUEUE_MAX,
    )
    return stats.report(phase)


def phase_isp() -> None:
    _log("isp", status="start", target_fps=TARGET_FPS)
    with dai.Device() as device:
        _log("isp", usb_speed=str(device.getUsbSpeed()))
        _drain_loop(device, _isp_pipeline(), phase="isp", stream_names=list(CAM_LIST), include_imu=False)


def _board_rev(device: dai.Device) -> str:
    try:
        eeprom = device.readCalibration().getEepromData()
        return str(getattr(eeprom, "boardRev", None) or getattr(eeprom, "boardName", "") or "")
    except Exception:
        return ""


def phase_h264() -> None:
    _log(
        "h264",
        status="start",
        target_fps=TARGET_FPS,
        bitrate_kbps=BITRATE_KBPS,
        imu_hz=IMU_HZ,
        gpio_fsync=int(OAK_GPIO_FSYNC),
    )
    with dai.Device() as device:
        _log("h264", usb_speed=str(device.getUsbSpeed()))
        board_rev = _board_rev(device)
        if board_rev:
            _log("h264", board_rev=board_rev)
        _drain_loop(
            device,
            _h264_pipeline(board_rev),
            phase="h264",
            stream_names=list(CAM_LIST),
            include_imu=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Luxonis OAK USB + ISP + H264 triplet bench")
    parser.add_argument(
        "--mode",
        choices=("usb", "isp", "h264", "all"),
        default="all",
        help="Benchmark phase (default: all)",
    )
    parser.add_argument("--duration", type=float, default=None, help="Seconds per isp/h264 phase")
    args = parser.parse_args()
    global DURATION_S
    if args.duration is not None:
        DURATION_S = float(args.duration)

    if args.mode in ("usb", "all"):
        phase_usb()
    if args.mode in ("isp", "all"):
        phase_isp()
    if args.mode in ("h264", "all"):
        phase_h264()


if __name__ == "__main__":
    main()
