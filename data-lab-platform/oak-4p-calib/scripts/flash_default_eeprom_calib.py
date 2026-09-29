#!/usr/bin/env python3
"""Flash placeholder EEPROM intrinsics from a template JSON (lab / new-hardware bootstrap).

Uses Luxonis ``CalibrationHandler.fromJson`` + ``Device.flashCalibration2``.
Template should contain valid ``cameraData`` for CAM_A..D (e.g. backed up from a
calibrated OAK-FFC-4P on the same board revision).

Example (on edge host, preview/capture stopped):

    python scripts/flash_default_eeprom_calib.py \\
        --template data/calib/calib_19443010113E6B2E00.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import depthai as dai


def pick_device(infos: list[dai.DeviceInfo]) -> dai.DeviceInfo | None:
    if not infos:
        return None
    booted = [i for i in infos if i.state == dai.XLinkDeviceState.X_LINK_BOOTED]
    return booted[0] if booted else infos[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Flash template EEPROM calibration to OAK")
    parser.add_argument(
        "--template",
        type=Path,
        required=True,
        help="EEPROM JSON from calibration_dump / read_calib backup",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("data/calib"),
        help="Directory to write post-flash backup (calib_<mxid>.json)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Load template only; do not flash")
    args = parser.parse_args()

    template_path = args.template.resolve()
    if not template_path.is_file():
        print(f"ERROR: template not found: {template_path}", file=sys.stderr)
        return 1

    template = json.loads(template_path.read_text(encoding="utf-8"))
    camera_data = template.get("cameraData") or []
    if not camera_data:
        print("ERROR: template has empty cameraData", file=sys.stderr)
        return 1

    handler = dai.CalibrationHandler.fromJson(template)
    print(f"Template: {template_path.name} ({len(camera_data)} camera entries)")

    info = pick_device(dai.XLinkConnection.getAllConnectedDevices())
    if info is None:
        print("ERROR: no OAK device (check USB; stop preview_standby / capture)", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"DRY RUN: would flash device mxid={info.getMxId()}")
        return 0

    with dai.Device(info) as device:
        mxid = device.getDeviceInfo().getMxId()
        print(f"Flashing mxid={mxid} ...")
        device.flashCalibration2(handler)
        print("flashCalibration2: OK")

        calib = device.readCalibration()
        ok = 0
        for sock in (
            dai.CameraBoardSocket.CAM_A,
            dai.CameraBoardSocket.CAM_B,
            dai.CameraBoardSocket.CAM_C,
            dai.CameraBoardSocket.CAM_D,
        ):
            if sock not in device.getConnectedCameras():
                continue
            try:
                dist = calib.getDistortionCoefficients(sock)
                intr, w, h = calib.getDefaultIntrinsics(sock)
                print(f"  {sock.name}: {w}x{h} fx={intr[0][0]:.1f} dist={dist[:4]}")
                ok += 1
            except RuntimeError as exc:
                print(f"  {sock.name}: FAIL {exc}")

        if ok < 4:
            print("WARNING: fewer than 4 sockets have calibration after flash", file=sys.stderr)
            return 1

        args.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = args.backup_dir / f"calib_{mxid}.json"
        calib.eepromToJsonFile(str(backup.resolve()))
        print(f"Backup: {backup}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
