#!/usr/bin/env python3
"""Validate strict 20Hz segment timing from rows.jsonl (offline acceptance)."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

PRIMARY_KEY_SUBSTRS = ("front_left", "head_left")
RGB_KEY_SUBSTRS = ("front_right", "rear_right", "head_right", "camera_02")
DEPTH_KEY_SUBSTRS = ("rear_left", "depth_head")

RGB_P99_MAX_MS = 16.0
RGB_ABS_MAX_MS = 33.0
DEPTH_P99_MAX_MS = 18.0
DEPTH_ABS_MAX_MS = 36.0


def _channel_for_key(key: str) -> str | None:
    if any(s in key for s in PRIMARY_KEY_SUBSTRS):
        return "primary"
    if any(s in key for s in DEPTH_KEY_SUBSTRS):
        return "depth"
    if any(s in key for s in RGB_KEY_SUBSTRS):
        return "rgb"
    return None


def _p99_ms(values: list[float]) -> float:
    if not values:
        return 0.0
    return sorted(values)[int(0.99 * (len(values) - 1))]


def analyze_rows(path: Path, *, interval_ms: float = 50.0) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < 2:
        return {"error": "too_few_rows", "path": str(path)}
    ts = [int(r["timestamp_ns"]) for r in rows]
    dts_ms = [(ts[i] - ts[i - 1]) / 1e6 for i in range(1, len(ts))]
    band_lo, band_hi = interval_ms - 1.0, interval_ms + 1.0
    in_band = sum(1 for d in dts_ms if band_lo <= d <= band_hi)

    rgb_ms: list[float] = []
    depth_ms: list[float] = []
    for r in rows:
        off = r.get("camera_ts_offset_ns") or {}
        for k, v in off.items():
            ch = _channel_for_key(k)
            ms = abs(int(v)) / 1e6
            if ch == "rgb":
                rgb_ms.append(ms)
            elif ch == "depth":
                depth_ms.append(ms)

    rgb_p99 = _p99_ms(rgb_ms)
    depth_p99 = _p99_ms(depth_ms)
    rgb_max = max(rgb_ms) if rgb_ms else 0.0
    depth_max = max(depth_ms) if depth_ms else 0.0

    rgb_ok = rgb_p99 <= RGB_P99_MAX_MS and rgb_max <= RGB_ABS_MAX_MS
    depth_ok = depth_p99 <= DEPTH_P99_MAX_MS and depth_max <= DEPTH_ABS_MAX_MS
    huge_ok = rgb_max <= 100.0 and depth_max <= 100.0

    return {
        "path": str(path),
        "frames": len(rows),
        "eff_hz": 1000.0 / statistics.mean(dts_ms),
        "dt_mean_ms": statistics.mean(dts_ms),
        "dt_std_ms": statistics.pstdev(dts_ms) if len(dts_ms) > 1 else 0.0,
        "dt_min_ms": min(dts_ms),
        "dt_max_ms": max(dts_ms),
        "pct_49_51": 100.0 * sum(1 for d in dts_ms if 49.0 <= d <= 51.0) / len(dts_ms),
        "pct_in_band": 100.0 * in_band / len(dts_ms),
        "rgb_offset_p99_ms": rgb_p99,
        "rgb_offset_max_ms": rgb_max,
        "depth_offset_p99_ms": depth_p99,
        "depth_offset_max_ms": depth_max,
        "rgb_ok": rgb_ok,
        "depth_ok": depth_ok,
        "huge_offset_ok": huge_ok,
        "cam_offset_p99_ms": max(rgb_p99, depth_p99),
        "cam_offset_max_ms": max(rgb_max, depth_max),
    }


def rows_ok(report: dict, *, interval_ms: float = 50.0) -> bool:
    if report.get("error"):
        return False
    timing_ok = (
        report.get("pct_49_51", 0) >= 99.9
        and abs(report.get("dt_mean_ms", 0) - interval_ms) <= 0.5
    )
    return bool(
        timing_ok
        and report.get("rgb_ok")
        and report.get("depth_ok")
        and report.get("huge_offset_ok", True)
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Validate strict 20Hz rows.jsonl timing")
    p.add_argument("rows_jsonl", type=Path, nargs="?", default=None)
    p.add_argument("--interval-ms", type=float, default=50.0)
    p.add_argument("--batch-dir", type=Path, default=None, help="Validate all seg_*/rows.jsonl under dir")
    args = p.parse_args()

    if args.batch_dir is not None:
        paths = sorted(args.batch_dir.glob("seg_*/rows.jsonl"))
        results = []
        for path in paths:
            report = analyze_rows(path, interval_ms=args.interval_ms)
            report["ok"] = rows_ok(report, interval_ms=args.interval_ms)
            results.append(report)
        pass_n = sum(1 for r in results if r.get("ok"))
        summary = {
            "tested": len(results),
            "pass": pass_n,
            "fail": len(results) - pass_n,
            "pass_rate_pct": 100.0 * pass_n / len(results) if results else 0.0,
        }
        print(json.dumps({"summary": summary, "segments": results}, indent=2))
        sys.exit(0 if pass_n == len(results) and results else 1)

    if args.rows_jsonl is None:
        p.error("rows_jsonl or --batch-dir required")
    report = analyze_rows(args.rows_jsonl, interval_ms=args.interval_ms)
    report["ok"] = rows_ok(report, interval_ms=args.interval_ms)
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
