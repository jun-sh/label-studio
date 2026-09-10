"""Device packet inter-arrival diagnostics for parallel-drain POC."""

from __future__ import annotations

import os
from collections import defaultdict


def device_ingest_diag_enabled() -> bool:
    return os.environ.get("EGO_DEVICE_INGEST_DIAG", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class DeviceIngestDiagnostics:
    def __init__(self) -> None:
        self._last_ts_ns: dict[str, int] = {}
        self._intervals_ms: dict[str, list[float]] = defaultdict(list)
        self._max_intervals = max(500, int(os.environ.get("EGO_DEVICE_INGEST_DIAG_SAMPLES", "2000")))

    def note_packet(self, cam_name: str, ts_ns: int) -> None:
        if not device_ingest_diag_enabled():
            return
        ts = int(ts_ns)
        prev = self._last_ts_ns.get(cam_name)
        if prev is not None and ts > prev:
            dt_ms = (ts - prev) / 1e6
            if 5.0 <= dt_ms <= 200.0:
                bucket = self._intervals_ms[cam_name]
                bucket.append(dt_ms)
                if len(bucket) > self._max_intervals:
                    del bucket[: len(bucket) - self._max_intervals]
        self._last_ts_ns[cam_name] = ts

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = int(round((len(ordered) - 1) * pct))
        return float(ordered[max(0, min(idx, len(ordered) - 1))])

    def report(self, *, prefix: str = "device_ingest_final") -> None:
        if not device_ingest_diag_enabled() or not self._intervals_ms:
            return
        parts: list[str] = []
        for cam in sorted(self._intervals_ms):
            vals = self._intervals_ms[cam]
            if not vals:
                continue
            p50 = self._percentile(vals, 0.50)
            p99 = self._percentile(vals, 0.99)
            eff_hz = 1000.0 / p50 if p50 > 0 else 0.0
            parts.append(f"{cam}:p50={p50:.2f}ms p99={p99:.2f}ms eff={eff_hz:.2f}hz n={len(vals)}")
        if parts:
            print(f"{prefix} " + " | ".join(parts), flush=True)
