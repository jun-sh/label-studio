#!/usr/bin/env python3
"""Minimal H.264 end-to-end verify: MCAP materialize + decode + timestamp mapping."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import av

ROOT = Path(__file__).resolve().parents[1]
DERIVE_DIR = ROOT / "lerobot-studio" / "derive"
MCAP_PATH_DEFAULT = (
    ROOT.parent
    / "data-storage/stream/ego-001/raw/segments/sess_2e8da6fee94a465e9a7fd79f8a6ab59e/seg_000001.mcap.zst"
)


def _load_materialize():
    spec = importlib.util.spec_from_file_location("mcap_materialize", DERIVE_DIR / "mcap-materialize.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _decode_h264(path: Path) -> int:
    count = 0
    with av.open(str(path), format="h264") as container:
        for _ in container.decode(video=0):
            count += 1
    return count


def _mcap_camera_timestamps(archive: Path, mod) -> dict[str, list[int]]:
    from mcap.reader import make_reader

    mcap_path, tmp = mod._decompress_if_needed(archive)
    out: dict[str, list[int]] = {k: [] for k in mod.CAMERA_TOPICS.values()}
    try:
        with open(mcap_path, "rb") as fp:
            reader = make_reader(fp)
            for _schema, channel, message in reader.iter_messages():
                cam = mod.CAMERA_TOPICS.get(channel.topic)
                if cam:
                    out[cam].append(int(message.log_time))
    finally:
        if tmp is not None:
            tmp.cleanup()
    for cam in out:
        out[cam].sort()
    return out


def verify(archive: Path, work_dir: Path) -> dict:
    mod = _load_materialize()
    work_dir.mkdir(parents=True, exist_ok=True)
    result = mod.materialize_mcap_archive(archive.resolve(), work_dir.resolve())
    trim = result.get("h264_trim") or {}
    warmup = trim.get("decode_warmup_packets") or {}
    row_count = int(result.get("frame_count") or 0)
    rows = [
        json.loads(line)
        for line in (work_dir / "rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    align_skip = int(trim.get("trim_content_packet_index") or 0)
    cam_ts = _mcap_camera_timestamps(archive, mod)

    camera_checks = []
    ts_mismatches = []
    for cam in mod.CAMERA_TOPICS.values():
        stream = work_dir / "streams" / f"{cam}.h264"
        decoded = _decode_h264(stream)
        content_frames = decoded - int(warmup.get(cam, 0))
        ok = content_frames == row_count == len(rows)
        camera_checks.append(
            {
                "camera": cam,
                "decoded_total": decoded,
                "decode_warmup": int(warmup.get(cam, 0)),
                "content_frames": content_frames,
                "expected_rows": row_count,
                "ok": ok,
            }
        )
        src_ts = cam_ts.get(cam, [])
        for i, row in enumerate(rows):
            src_idx = align_skip + i
            if src_idx >= len(src_ts):
                ts_mismatches.append({"camera": cam, "frame_index": i, "reason": "src_index_oob"})
                continue
            row_ts = int(row.get("timestamp_ns") or 0)
            if row_ts and row_ts != src_ts[src_idx]:
                ts_mismatches.append(
                    {
                        "camera": cam,
                        "frame_index": i,
                        "row_timestamp_ns": row_ts,
                        "mcap_log_time_ns": src_ts[src_idx],
                    }
                )

    ok = all(c["ok"] for c in camera_checks) and not ts_mismatches
    return {
        "ok": ok,
        "archive": str(archive.resolve()),
        "session_id": result.get("session_id"),
        "segment_id": result.get("segment_id"),
        "frame_count": row_count,
        "h264_trim": trim,
        "camera_checks": camera_checks,
        "timestamp_mapping": {
            "align_skip": align_skip,
            "mismatches": ts_mismatches[:20],
            "mismatch_count": len(ts_mismatches),
            "ok": len(ts_mismatches) == 0,
        },
        "work_dir": str(work_dir.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="H.264 MCAP materialize e2e verify")
    parser.add_argument("archive", type=Path, nargs="?", default=MCAP_PATH_DEFAULT)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.archive, args.work_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(args.report.resolve())}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
