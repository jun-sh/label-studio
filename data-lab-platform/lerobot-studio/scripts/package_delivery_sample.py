#!/usr/bin/env python3
"""Package golden delivery sample: segment tar.zst + mcap + manifest (DROID-style bundle)."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package_delivery_sample(
    *,
    tar_zst: Path,
    mcap: Path,
    out_dir: Path,
    station_id: str,
    session_id: str,
    segment_id: str,
    notes: str = "",
) -> dict[str, object]:
    tar_zst = Path(tar_zst).resolve()
    mcap = Path(mcap).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    sample_name = f"{station_id}__{session_id}__{segment_id}"
    bundle_dir = out_dir / sample_name
    bundle_dir.mkdir(parents=True, exist_ok=True)

    tar_dest = bundle_dir / tar_zst.name
    mcap_dest = bundle_dir / mcap.name
    shutil.copy2(tar_zst, tar_dest)
    shutil.copy2(mcap, mcap_dest)

    validation = mcap.with_suffix(".imu_validation.json")
    if validation.is_file():
        shutil.copy2(validation, bundle_dir / validation.name)

    manifest = {
        "schema": "ego_dual_delivery_sample_v1",
        "aligned_with": "DROID-style dual artifact delivery",
        "station_id": station_id,
        "session_id": session_id,
        "segment_id": segment_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": {
            "lerobot_source": {
                "path": tar_dest.name,
                "sha256": sha256_file(tar_dest),
                "role": "30Hz grid-aligned training source (tar.zst segment)",
            },
            "mcap_sensor_log": {
                "path": mcap_dest.name,
                "sha256": sha256_file(mcap_dest),
                "role": "ROS2 sensor black-box log (JPEG + IMU + calibration metadata)",
            },
        },
        "notes": notes or "PR1+PR2 acceptance golden sample",
    }
    manifest_path = bundle_dir / "delivery_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "bundle_dir": str(bundle_dir), "manifest": str(manifest_path), **manifest}


def main() -> None:
    p = argparse.ArgumentParser(description="Package tar.zst + mcap delivery golden sample")
    p.add_argument("--tar-zst", type=Path, required=True)
    p.add_argument("--mcap", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--station-id", default="ego-lan-214")
    p.add_argument("--session-id", required=True)
    p.add_argument("--segment-id", required=True)
    p.add_argument("--notes", default="")
    args = p.parse_args()
    report = package_delivery_sample(
        tar_zst=args.tar_zst,
        mcap=args.mcap,
        out_dir=args.out_dir,
        station_id=args.station_id,
        session_id=args.session_id,
        segment_id=args.segment_id,
        notes=args.notes,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
