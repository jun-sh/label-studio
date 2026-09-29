#!/usr/bin/env python3
"""
Preview undistorted frames from 4 fisheye cameras.

RVC4 (OAK-4P): uses device-side enableUndistortion on Camera node.
RVC2 (OAK-FFC-4P): uses host-side cv2.fisheye.remap with EEPROM calibration.
"""

import argparse

import cv2
import depthai as dai
import numpy as np

ALL_SOCKETS = (
    dai.CameraBoardSocket.CAM_A,
    dai.CameraBoardSocket.CAM_B,
    dai.CameraBoardSocket.CAM_C,
    dai.CameraBoardSocket.CAM_D,
)


def pick_device(infos: list) -> dai.DeviceInfo | None:
    if not infos:
        return None
    booted = [i for i in infos if i.state == dai.XLinkDeviceState.X_LINK_BOOTED]
    return booted[0] if booted else infos[0]


def build_fisheye_maps(calib: dai.CalibrationHandler, sock: dai.CameraBoardSocket, size: tuple[int, int]):
    w, h = size
    k = np.array(calib.getCameraIntrinsics(sock, w, h), dtype=np.float64)
    d = np.array(calib.getDistortionCoefficients(sock), dtype=np.float64)[:4]
    r = np.eye(3)
    p = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(k, d, (w, h), r, balance=0.0)
    map_x, map_y = cv2.fisheye.initUndistortRectifyMap(k, d, r, p, (w, h), cv2.CV_32FC1)
    return map_x, map_y


def run_rvc4(device: dai.Device, width: int, height: int) -> None:
    with dai.Pipeline(device) as pipeline:
        queues = {}
        for sock in ALL_SOCKETS:
            if sock not in device.getConnectedCameras():
                continue
            cam = pipeline.create(dai.node.Camera).build(sock)
            queues[sock.name] = cam.requestOutput(
                (width, height),
                resizeMode=dai.ImgResizeMode.LETTERBOX,
                enableUndistortion=True,
            ).createOutputQueue()

        pipeline.start()
        while pipeline.isRunning():
            for name, queue in queues.items():
                frame = queue.tryGet()
                if frame is not None:
                    cv2.imshow(f"undistorted_{name}", frame.getCvFrame())
            if cv2.waitKey(1) == ord("q"):
                break


def run_rvc2(device: dai.Device, width: int, height: int) -> None:
    calib = device.readCalibration()
    pipeline = dai.Pipeline()
    queues = {}
    maps = {}

    for sock in ALL_SOCKETS:
        if sock not in device.getConnectedCameras():
            continue
        cam = pipeline.create(dai.node.MonoCamera)
        cam.setBoardSocket(sock)
        cam.setResolution(dai.MonoCameraProperties.SensorResolution.THE_800_P)
        cam.setFps(20)

        xout = pipeline.create(dai.node.XLinkOut)
        xout.setStreamName(sock.name)
        cam.out.link(xout.input)
        queues[sock.name] = device.getOutputQueue(sock.name, maxSize=4, blocking=False)

    device.startPipeline(pipeline)

    while True:
        for name, queue in queues.items():
            pkt = queue.tryGet()
            if pkt is None:
                continue
            gray = pkt.getCvFrame()
            h, w = gray.shape[:2]
            if name not in maps:
                sock = dai.CameraBoardSocket[name]
                maps[name] = build_fisheye_maps(calib, sock, (w, h))
            rect = cv2.remap(gray, maps[name][0], maps[name][1], cv2.INTER_LINEAR)
            cv2.imshow(f"undistorted_{name}", rect)

        if cv2.waitKey(1) == ord("q"):
            break


def main() -> int:
    parser = argparse.ArgumentParser(description="4-camera fisheye undistort preview")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=400)
    args = parser.parse_args()

    info = pick_device(dai.XLinkConnection.getAllConnectedDevices())
    if info is None:
        print("No device found.")
        return 1

    with dai.Device(info) as device:
        print(f"Device: {device.getDeviceName()} ({device.getDeviceId()})")
        if info.platform == dai.XLinkPlatform.X_LINK_RVC4:
            print("Mode: device-side undistort (RVC4)")
            run_rvc4(device, args.width, args.height)
        else:
            print("Mode: host-side fisheye remap (RVC2)")
            run_rvc2(device, args.width, args.height)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
