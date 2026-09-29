#!/usr/bin/env python3
import depthai as dai

ALL_SOCKETS = (
    dai.CameraBoardSocket.CAM_A,
    dai.CameraBoardSocket.CAM_B,
    dai.CameraBoardSocket.CAM_C,
    dai.CameraBoardSocket.CAM_D,
)


def pick_device(infos: list[dai.DeviceInfo]) -> dai.DeviceInfo | None:
    if not infos:
        return None
    booted = [i for i in infos if i.state == dai.XLinkDeviceState.X_LINK_BOOTED]
    return booted[0] if booted else infos[0]


def main() -> int:
    infos = dai.XLinkConnection.getAllConnectedDevices()
    print(f"XLink devices: {len(infos)}")
    for info in infos:
        print(
            f"  id={info.deviceId} name={info.name} "
            f"platform={info.platform} state={info.state}"
        )

    info = pick_device(infos)
    if info is None:
        print("No device found. Check USB cable, power, and udev rules.")
        return 1

    if info.state == dai.XLinkDeviceState.X_LINK_BOOTLOADER:
        print("WARNING: device is in BOOTLOADER state (not fully booted).")

    with dai.Device(info) as device:
        print(f"DeviceId:   {device.getDeviceId()}")
        print(f"Name:       {device.getDeviceName()}")
        print(f"Cameras:    {device.getConnectedCameras()}")

        try:
            board = device.readCalibration().getEepromData().boardName
            print(f"Board:      {board}")
        except Exception as exc:
            print(f"Board:      (read failed: {exc})")

        missing = set(ALL_SOCKETS) - set(device.getConnectedCameras())
        if missing:
            print(f"WARNING: missing sockets: {missing}")
        else:
            print("OK: all 4 camera sockets detected")

        if info.platform == dai.XLinkPlatform.X_LINK_MYRIAD_X:
            print("Platform:   RVC2 (Myriad X) — e.g. OAK-FFC-4P")
        elif info.platform == dai.XLinkPlatform.X_LINK_RVC4:
            print("Platform:   RVC4 — e.g. OAK-4P-New")
        else:
            print(f"Platform:   {info.platform}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
