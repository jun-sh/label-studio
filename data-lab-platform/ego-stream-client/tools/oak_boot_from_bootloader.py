#!/usr/bin/env python3
"""Recover OAK device from USB Bootloader (03e7:f63c) to runtime via DepthAI."""

from __future__ import annotations

import sys
import time

import depthai as dai


def main() -> int:
    ret, info = dai.DeviceBootloader.getFirstAvailableDevice()
    if not ret:
        devs = dai.Device.getAllAvailableDevices()
        if devs and "BOOTLOADER" not in str(devs[0].state):
            print(f"OK: device already runtime {devs[0].mxid} {devs[0].state}")
            return 0
        print("ERROR: no OAK bootloader or runtime device found", file=sys.stderr)
        return 1

    print(f"bootloader: mxid={info.mxid} state={info.state}")
    with dai.DeviceBootloader(info) as bl:
        bl.bootUsbRomBootloader()
    print("bootUsbRomBootloader() sent, waiting for runtime...")

    for i in range(12):
        time.sleep(1)
        devs = dai.Device.getAllAvailableDevices()
        states = [(d.mxid, str(d.state)) for d in devs]
        print(f"  t+{i+1}s {states}")
        if devs and "BOOTLOADER" not in str(devs[0].state):
            try:
                with dai.Device(devs[0]) as dev:
                    print(f"OK: runtime mxid={dev.getMxId()}")
                return 0
            except Exception as exc:
                print(f"warn: state ok but Device() failed: {exc}")

    print("ERROR: still not runtime after boot", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
