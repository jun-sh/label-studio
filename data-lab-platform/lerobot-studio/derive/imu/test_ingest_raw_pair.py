#!/usr/bin/env python3
"""Unit tests for ingest-raw IMU accel/gyro pairing tolerance."""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from pathlib import Path

INGEST_RAW = Path(__file__).resolve().parent / "ingest-raw.py"
spec = importlib.util.spec_from_file_location("ingest_raw", INGEST_RAW)
ingest_raw = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ingest_raw)


def _pair(delta_ms: float) -> tuple[int, int, int]:
    """One accel + one gyro separated by delta_ms; return (both, nan_accel, nan_gyro) row counts."""
    base = 1_000_000_000
    delta_ns = int(delta_ms * 1_000_000)
    lines = [
        {"ts_ns": base, "sensor": "accel", "x": 1.0, "y": 0.0, "z": 0.0},
        {"ts_ns": base + delta_ns, "sensor": "gyro", "x": 0.0, "y": 1.0, "z": 0.0},
    ]
    paired = ingest_raw.pair_imu_records(lines)
    both = sum(1 for _, p in paired if p["accel"] and p["gyro"])
    rows = ingest_raw.jsonl_to_vector_rows(lines, segment_id="seg_t")
    nan_a = sum(1 for r in rows if any(math.isnan(v) for v in r["accel"]))
    nan_g = sum(1 for r in rows if any(math.isnan(v) for v in r["gyro"]))
    return both, nan_a, nan_g


class TestPairImuTolerance(unittest.TestCase):
    def test_tolerance_constant_is_3ms(self) -> None:
        self.assertEqual(ingest_raw.PAIR_TOLERANCE_NS, 3_000_000)

    def test_merge_within_0_5ms(self) -> None:
        both, nan_a, nan_g = _pair(0.5)
        self.assertEqual(both, 1)
        self.assertEqual(nan_a, 0)
        self.assertEqual(nan_g, 0)

    def test_merge_within_1_5ms(self) -> None:
        both, nan_a, nan_g = _pair(1.5)
        self.assertEqual(both, 1)
        self.assertEqual(nan_a, 0)
        self.assertEqual(nan_g, 0)

    def test_merge_within_2_6ms(self) -> None:
        both, nan_a, nan_g = _pair(2.6)
        self.assertEqual(both, 1)
        self.assertEqual(nan_a, 0)
        self.assertEqual(nan_g, 0)

    def test_split_beyond_3ms(self) -> None:
        both, _nan_a, _nan_g = _pair(4.0)
        self.assertEqual(both, 0)
        base = 1_000_000_000
        lines = [
            {"ts_ns": base, "sensor": "accel", "x": 1.0, "y": 0.0, "z": 0.0},
            {"ts_ns": base + 4_000_000, "sensor": "gyro", "x": 0.0, "y": 1.0, "z": 0.0},
        ]
        paired = ingest_raw.pair_imu_records(lines)
        split_rows = sum(
            1
            for _, parts in paired
            if (parts["accel"] and not parts["gyro"]) or (parts["gyro"] and not parts["accel"])
        )
        self.assertGreaterEqual(split_rows, 2)
        self.assertEqual(len(ingest_raw.jsonl_to_vector_rows(lines, segment_id="seg_t")), 0)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
