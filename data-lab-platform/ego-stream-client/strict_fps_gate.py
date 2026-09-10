"""Strict 30Hz effective fps gate for closed MCAP segments."""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path
from typing import Any

DEFAULT_INTERVAL_MS = 33.0
TOPIC_OBS_STATE = "/ego/observation/state"
TOPIC_IMU_RAW = "/ego/imu/raw"


def _eff_hz_band() -> tuple[float, float]:
    lo = float(os.environ.get("EGO_STRICT_EFF_HZ_LO", "29.8"))
    hi = float(os.environ.get("EGO_STRICT_EFF_HZ_HI", "30.2"))
    return lo, hi


def timeline_gate_enabled() -> bool:
    """Whether a compressed timeline should fail the segment, rather than just warn.

    Off by default: enabling it turns a pre-existing silent defect into rejected
    segments, which would stall the upload chain for every recording made while the
    capture rate is still short of the grid rate.
    """
    return os.environ.get("EGO_TIMELINE_GATE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _timeline_max_dev() -> float:
    return float(os.environ.get("EGO_TIMELINE_MAX_DEV", "0.03"))


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


def _read_mcap_series(mcap_path: Path) -> tuple[list[int], list[int]]:
    """Read the grid timestamps and the IMU device timestamps in a single pass."""
    from mcap.reader import make_reader

    grid_out: list[int] = []
    imu_out: list[int] = []
    with open(mcap_path, "rb") as fp:
        reader = make_reader(fp)
        for _schema, channel, message in reader.iter_messages():
            if channel.topic == TOPIC_IMU_RAW:
                # The writer stores each IMU record's ts_ns as its log_time, so the span
                # is available without decoding ~27k JSON payloads per segment. This
                # runs during segment rotation, so the saving matters. A zero log_time
                # means the writer did not stamp it; too few usable samples degrades to
                # "no reference" rather than a wrong answer.
                if message.log_time:
                    imu_out.append(int(message.log_time))
                continue
            if channel.topic != TOPIC_OBS_STATE:
                continue
            try:
                payload = json.loads(message.data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            ts = payload.get("timestamp_ns")
            if ts is not None:
                grid_out.append(int(ts))
    return grid_out, imu_out


def _read_mcap_obs_timestamps(mcap_path: Path) -> list[int]:
    return _read_mcap_series(mcap_path)[0]


def timeline_from_writer_spans(
    *,
    frame_count: int,
    grid_min_ns: int | None,
    grid_max_ns: int | None,
    imu_min_ns: int | None,
    imu_max_ns: int | None,
    imu_samples: int,
) -> dict[str, Any]:
    """O(1) timeline coherence from writer min/max spans (no MCAP re-read)."""
    if frame_count < 2:
        return {"error": "too_few_frames", "grid_frames": frame_count, "source": "writer_spans"}
    if grid_min_ns is None or grid_max_ns is None:
        return {"error": "missing_grid_span", "grid_frames": frame_count, "source": "writer_spans"}
    if imu_min_ns is None or imu_max_ns is None or imu_samples < 2:
        return {
            "error": "no_imu_reference",
            "imu_samples": imu_samples,
            "source": "writer_spans",
        }
    grid_span_s = (int(grid_max_ns) - int(grid_min_ns)) / 1e9
    imu_span_s = (int(imu_max_ns) - int(imu_min_ns)) / 1e9
    if grid_span_s <= 0 or imu_span_s <= 0:
        return {
            "error": "degenerate_span",
            "grid_span_s": grid_span_s,
            "imu_span_s": imu_span_s,
            "source": "writer_spans",
        }
    ratio = imu_span_s / grid_span_s
    max_dev = _timeline_max_dev()
    return {
        "source": "writer_spans",
        "grid_frames": frame_count,
        "grid_span_s": grid_span_s,
        "imu_samples": imu_samples,
        "imu_span_s": imu_span_s,
        "real_fps": frame_count / imu_span_s,
        "claimed_fps": frame_count / grid_span_s,
        "ratio": ratio,
        "max_dev": max_dev,
        "ok": abs(ratio - 1.0) <= max_dev,
    }


def timeline_integrity_issues(timeline: dict[str, Any]) -> list[str]:
    """Flag compressed segments even when EGO_TIMELINE_GATE is off."""
    if not timeline:
        return []
    if timeline.get("error"):
        return [f"timeline_unavailable:{timeline['error']}"]
    if timeline.get("ok"):
        return []
    return [
        "timeline_incoherent:"
        f"ratio={timeline.get('ratio', 0):.4f},"
        f"real_fps={timeline.get('real_fps', 0):.3f},"
        f"claimed_fps={timeline.get('claimed_fps', 0):.3f}",
    ]


def log_timeline_verdict(segment_name: str, timeline: dict[str, Any]) -> None:
    if timeline.get("error"):
        print(f"[timeline] {segment_name}: unavailable ({timeline['error']})", flush=True)
        return
    verdict = "ok" if timeline.get("ok") else "COMPRESSED"
    print(
        f"[timeline] {segment_name}: {verdict} ratio={timeline.get('ratio', 0):.4f} "
        f"real_fps={timeline.get('real_fps', 0):.3f} claimed_fps={timeline.get('claimed_fps', 0):.3f} "
        f"real_span={timeline.get('imu_span_s', 0):.2f}s "
        f"recorded_span={timeline.get('grid_span_s', 0):.2f}s "
        f"source={timeline.get('source', 'unknown')}",
        flush=True,
    )


def analyze_timeline_coherence(grid_ns: list[int], imu_ns: list[int]) -> dict[str, Any]:
    """Compare recorded time against real elapsed time.

    The grid timestamps written to MCAP are synthesised at a fixed interval, so they
    carry no information about how long the recording actually took: a segment captured
    at 26fps and one captured at 30fps produce byte-identical timestamp spacing. That
    makes the strict fps gate blind to a rate shortfall, because it validates the grid
    against itself. The IMU stream is the only series in the file carrying device time,
    so its span is used as the real-elapsed-time reference.

    ratio > 1 means the recording claims less time than it took, i.e. playback and any
    timestamp-derived velocity are that much too fast.
    """
    if len(grid_ns) < 2:
        return {"error": "too_few_grid_timestamps", "grid_frames": len(grid_ns)}
    if len(imu_ns) < 2:
        return {"error": "no_imu_reference", "imu_samples": len(imu_ns)}
    grid_span_s = (max(grid_ns) - min(grid_ns)) / 1e9
    imu_span_s = (max(imu_ns) - min(imu_ns)) / 1e9
    if grid_span_s <= 0 or imu_span_s <= 0:
        return {"error": "degenerate_span", "grid_span_s": grid_span_s, "imu_span_s": imu_span_s}
    ratio = imu_span_s / grid_span_s
    max_dev = _timeline_max_dev()
    return {
        "grid_frames": len(grid_ns),
        "grid_span_s": grid_span_s,
        "imu_samples": len(imu_ns),
        "imu_span_s": imu_span_s,
        "real_fps": len(grid_ns) / imu_span_s,
        "claimed_fps": len(grid_ns) / grid_span_s,
        "ratio": ratio,
        "max_dev": max_dev,
        "ok": abs(ratio - 1.0) <= max_dev,
    }


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
    if not strict_fps_gate_enabled() and not timeline_gate_enabled():
        return True, []
    if not mcap_path.is_file():
        return False, ["missing_segment_mcap"]
    issues: list[str] = []
    try:
        grid_ns, imu_ns = _read_mcap_series(mcap_path)
    except Exception as exc:
        # A segment truncated by an abrupt stop makes the reader raise while parsing
        # the footer (RecordLengthLimitExceeded). This runs inside segment integrity
        # checking, so letting it propagate takes down the capture process and it comes
        # back to the same unreadable file: that is what put ego-001 in a restart loop.
        # An unreadable segment is a corrupt segment, not a reason to stop recording.
        return False, [f"mcap_unreadable:{type(exc).__name__}"]

    # Timeline coherence reads every IMU sample and takes ~4s on a closed segment.
    # It belongs on the upload/QC path, not on the capture rotation hot path, unless
    # EGO_TIMELINE_GATE is explicitly enabled to reject compressed segments.
    if timeline_gate_enabled():
        timeline = analyze_timeline_coherence(grid_ns, imu_ns)
        if timeline.get("error"):
            print(f"[timeline] {mcap_path.name}: unavailable ({timeline['error']})", flush=True)
        else:
            verdict = "ok" if timeline["ok"] else "COMPRESSED"
            print(
                f"[timeline] {mcap_path.name}: {verdict} ratio={timeline['ratio']:.4f} "
                f"real_fps={timeline['real_fps']:.3f} claimed_fps={timeline['claimed_fps']:.3f} "
                f"real_span={timeline['imu_span_s']:.2f}s recorded_span={timeline['grid_span_s']:.2f}s",
                flush=True,
            )
            if not timeline["ok"]:
                issues.append(
                    "timeline_incoherent:"
                    f"ratio={timeline['ratio']:.4f},"
                    f"real_fps={timeline['real_fps']:.3f},"
                    f"claimed_fps={timeline['claimed_fps']:.3f}",
                )

    if not strict_fps_gate_enabled():
        return len(issues) == 0, issues

    report = analyze_timestamp_series(grid_ns, interval_ms=interval_ms)
    report["path"] = str(mcap_path)
    report["ok"] = strict_timestamp_series_ok(report, interval_ms=interval_ms)
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


def audit_timeline(mcap_path: Path) -> dict[str, Any]:
    grid_ns, imu_ns = _read_mcap_series(mcap_path)
    report = analyze_timeline_coherence(grid_ns, imu_ns)
    report["path"] = str(mcap_path)
    return report


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        for path in sorted(Path().glob(arg)) if any(c in arg for c in "*?[") else [Path(arg)]:
            print(json.dumps(audit_timeline(path)))
