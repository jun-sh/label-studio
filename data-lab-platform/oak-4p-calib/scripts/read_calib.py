#!/usr/bin/env python3
import argparse
from pathlib import Path

import depthai as dai

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


def socket_name(sock: dai.CameraBoardSocket) -> str:
    return sock.name


def main() -> int:
    parser = argparse.ArgumentParser(description="Read OAK fisheye calibration from EEPROM")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("data/calib"),
        help="Directory for JSON backup",
    )
    args = parser.parse_args()

    info = pick_device(dai.XLinkConnection.getAllConnectedDevices())
    if info is None:
        print("No device found.")
        return 1

    with dai.Device(info) as device:
        device_id = device.getDeviceId()
        calib = device.readCalibration()
        eeprom = calib.getEepromData()

        print(f"DeviceId: {device_id}")
        print(f"Name:     {device.getDeviceName()}")
        print(f"Board:    {eeprom.boardName}")
        print()

        ok_count = 0
        for sock in ALL_SOCKETS:
            if sock not in device.getConnectedCameras():
                print(f"[{socket_name(sock)}] not connected, skip")
                continue

            try:
                model = calib.getDistortionModel(sock)
                dist = calib.getDistortionCoefficients(sock)
                intrinsics, width, height = calib.getDefaultIntrinsics(sock)
                fov = calib.getFov(sock)
            except RuntimeError as exc:
                print(f"[{socket_name(sock)}] no calibration data: {exc}")
                continue

            print(f"[{socket_name(sock)}]")
            print(f"  model:      {model}")
            print(f"  resolution: {width}x{height}")
            print(f"  fov:        {fov:.2f}")
            print(f"  dist:       {dist}")
            print(f"  K:          {intrinsics}")
            print()
            ok_count += 1

        if ok_count == 0:
            print("ERROR: no camera calibration in EEPROM — Charuco recalibration required.")
            return 1

        args.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = args.output_dir / f"calib_{device_id}.json"
        calib.eepromToJsonFile(str(out_path.resolve()))
        print(f"Saved: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
