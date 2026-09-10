"""Strict 30Hz effective fps gate for closed MCAP segments."""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path
from typing import Any

DEFAULT_INTERVAL_MS = 33.0
TOPIC_OBS_STATE = "/ego/observation/state"


def _eff_hz_band() -> tuple[float, float]:
    lo = float(os.environ.get("EGO_STRICT_EFF_HZ_LO", "29.8"))
    hi = float(os.environ.get("EGO_STRICT_EFF_HZ_HI", "30.2"))
    return lo, hi


def strict_fps_gate_enabled() -> bool:
    return os.environ.get("EGO_STRICT_FPS_GATE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def analyze_timestamp_series(
    ts_ns: list[int],
    *,
    interval_ms: float = DEFAULT_INTERVAL_MS,
) -> dict[str, Any]:
    if len(ts_ns) < 2:
        return {"error": "too_few_timestamps", "frames": len(ts_ns)}
    dts_ms = [(ts_ns[i] - ts_ns[i - 1]) / 1e6 for i in range(1, len(ts_ns))]
    tight_lo, tight_hi = interval_ms - 0.5, interval_ms + 0.5
    eff_hz = 1000.0 / statistics.mean(dts_ms)
    lo, hi = _eff_hz_band()
    return {
        "frames": len(ts_ns),
        "eff_hz": eff_hz,
        "dt_mean_ms": statistics.mean(dts_ms),
        "dt_std_ms": statistics.pstdev(dts_ms) if len(dts_ms) > 1 else 0.0,
        "pct_in_tight_band": 100.0
        * sum(1 for d in dts_ms if tight_lo <= d <= tight_hi)
        / len(dts_ms),
        "eff_hz_lo": lo,
        "eff_hz_hi": hi,
        "eff_hz_ok": lo <= eff_hz <= hi,
    }


def strict_timestamp_series_ok(report: dict[str, Any], *, interval_ms: float = DEFAULT_INTERVAL_MS) -> bool:
    if report.get("error"):
        return False
    dt_ok = abs(report.get("dt_mean_ms", 0) - interval_ms) <= 0.5
    band_ok = report.get("pct_in_tight_band", 0) >= 99.0
    if band_ok and dt_ok:
        return True
    lo, hi = _eff_hz_band()
    return bool(band_ok and dt_ok and lo <= report.get("eff_hz", 0) <= hi)


def _read_mcap_obs_timestamps(mcap_path: Path) -> list[int]:
    from mcap.reader import make_reader

    ts_out: list[int] = []
    with open(mcap_path, "rb") as fp:
        reader = make_reader(fp)
        for _schema, channel, message in reader.iter_messages():
            if channel.topic != TOPIC_OBS_STATE:
                continue
            try:
                payload = json.loads(message.data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            ts = payload.get("timestamp_ns")
            if ts is None:
                continue
            ts_out.append(int(ts))
    return ts_out


def analyze_mcap_strict_fps(
    mcap_path: Path,
    *,
    interval_ms: float = DEFAULT_INTERVAL_MS,
) -> dict[str, Any]:
    ts_ns = _read_mcap_obs_timestamps(mcap_path)
    report = analyze_timestamp_series(ts_ns, interval_ms=interval_ms)
    report["path"] = str(mcap_path)
    report["ok"] = strict_timestamp_series_ok(report, interval_ms=interval_ms)
    return report


def check_mcap_strict_fps(
    mcap_path: Path,
    frame_count: int,
    *,
    interval_ms: float = DEFAULT_INTERVAL_MS,
) -> tuple[bool, list[str]]:
    if not strict_fps_gate_enabled():
        return True, []
    if not mcap_path.is_file():
        return False, ["missing_segment_mcap"]
    report = analyze_mcap_strict_fps(mcap_path, interval_ms=interval_ms)
    issues: list[str] = []
    if report.get("error"):
        issues.append(str(report["error"]))
        return False, issues
    obs_frames = int(report.get("frames") or 0)
    if obs_frames != int(frame_count):
        issues.append(f"mcap_obs_frame_count_mismatch:{obs_frames}!={frame_count}")
    if not report.get("ok"):
        issues.append(
            "strict_fps_gate_failed:"
            f"eff_hz={report.get('eff_hz', 0):.3f},"
            f"pct_tight={report.get('pct_in_tight_band', 0):.1f},"
            f"dt_mean_ms={report.get('dt_mean_ms', 0):.3f}",
        )
    return len(issues) == 0, issues
