# coding=utf-8
"""Manufacturer-style 4x AR0234 ISP baseline (no disk, loose per-cam tryGet). Run on 214 with capture stopped."""
from __future__ import annotations

import collections
import time

import cv2
import depthai as dai

cam_list = {
    "CAM_A": {"color": True, "res": "1200"},
    "CAM_B": {"color": True, "res": "1200"},
    "CAM_C": {"color": True, "res": "1200"},
    "CAM_D": {"color": True, "res": "1200"},
}

color_res_opts = {
    "1200": dai.ColorCameraProperties.SensorResolution.THE_1200_P,
}

cam_socket_opts = {
    "CAM_A": dai.CameraBoardSocket.CAM_A,
    "CAM_B": dai.CameraBoardSocket.CAM_B,
    "CAM_C": dai.CameraBoardSocket.CAM_C,
    "CAM_D": dai.CameraBoardSocket.CAM_D,
}

TARGET_FPS = 20


def create_pipeline() -> dai.Pipeline:
    pipeline = dai.Pipeline()
    pipeline.setXLinkChunkSize(0)
    for cam_name, cam_props in cam_list.items():
        xout = pipeline.create(dai.node.XLinkOut)
        xout.setStreamName(cam_name)
        cam = pipeline.create(dai.node.ColorCamera)
        cam.setResolution(color_res_opts[cam_props["res"]])
        cam.setIspScale(2, 3)
        cam.setInterleaved(False)
        cam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
        cam.isp.link(xout.input)
        cam.setBoardSocket(cam_socket_opts[cam_name])
        cam.setFps(TARGET_FPS)
    return pipeline


class FPSHandler:
    def __init__(self, max_ticks: int = 100) -> None:
        self._ticks: dict[str, collections.deque[float]] = {}

    def tick(self, name: str) -> None:
        if name not in self._ticks:
            self._ticks[name] = collections.deque(maxlen=100)
        self._ticks[name].append(time.monotonic())

    def tick_fps(self, name: str) -> float:
        q = self._ticks.get(name)
        if not q or len(q) < 2:
            return 0.0
        dt = q[-1] - q[0]
        return (len(q) - 1) / dt if dt > 0 else 0.0

    def print_status(self) -> None:
        for name in sorted(self._ticks):
            print(f"  [{name}]: {self.tick_fps(name):.1f} fps")


def main() -> None:
    duration_s = float(__import__("os").environ.get("BASELINE_DURATION_S", "30"))
    with dai.Device() as device:
        print(f"DepthAI {dai.__version__} USB={device.getUsbSpeed()}")
        device.startPipeline(create_pipeline())
        fps_handler = FPSHandler()
        queues = {
            name: device.getOutputQueue(name, maxSize=4, blocking=False) for name in cam_list
        }
        t_end = time.monotonic() + duration_s
        while time.monotonic() < t_end and not device.isClosed():
            for cam_name in cam_list:
                pkt = queues[cam_name].tryGet()
                while pkt is not None:
                    fps_handler.tick(f"FRAME_{cam_name}")
                    frame = pkt.getCvFrame()
                    if frame is not None:
                        h, w = frame.shape[:2]
                        if fps_handler.tick_fps(f"FRAME_{cam_name}") > 0 and pkt.getSequenceNum() % 30 == 0:
                            print(f"{cam_name} {w}x{h} ts={pkt.getTimestampDevice()}")
                    pkt = queues[cam_name].tryGet()
        print(f"=== {duration_s:.0f}s baseline ===")
        fps_handler.print_status()
        rates = [fps_handler.tick_fps(f"FRAME_{n}") for n in cam_list]
        print(f"min_cam_fps={min(rates):.1f} avg_cam_fps={sum(rates)/len(rates):.1f}")


if __name__ == "__main__":
    main()
