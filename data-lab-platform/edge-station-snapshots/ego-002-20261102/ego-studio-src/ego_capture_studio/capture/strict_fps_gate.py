"""Strict 30Hz effective fps gate for closed MCAP segments."""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path
from typing import Any

DEFAULT_INTERVAL_MS = 1000.0 / 30.0
DEFAULT_INTERVAL_NS = 33_333_333
TOPIC_OBS_STATE = "/ego/observation/state"
TOPIC_IMU_RAW = "/ego/imu/raw"


def capture_interval_ms() -> float:
    """Configured capture grid interval (ms); production ego-001 uses 33."""
    return float(os.environ.get("EGO_FRAME_INTERVAL_MS", str(DEFAULT_INTERVAL_MS)))


def _eff_hz_band(*, interval_ms: float | None = None) -> tuple[float, float]:
    ms = capture_interval_ms() if interval_ms is None else float(interval_ms)
    nominal_hz = 1000.0 / ms
    lo = float(os.environ.get("EGO_STRICT_EFF_HZ_LO", str(nominal_hz - 0.503)))
    hi = float(os.environ.get("EGO_STRICT_EFF_HZ_HI", str(nominal_hz + 0.1)))
    # 33ms grid is 30.303Hz — env band 29.8–30.2 must not reject nominal spacing.
    hi = max(hi, nominal_hz + 0.05)
    lo = min(lo, nominal_hz - 0.05)
    return lo, hi


def timeline_gate_enabled() -> bool:
    """Legacy: timeline blocks capture close (pre–QA-layering behaviour).

    Prefer EGO_TIMELINE_CAPTURE_BLOCK=0 + EGO_TIMELINE_UPLOAD_QC=1 (commercial default).
    """
    return os.environ.get("EGO_TIMELINE_GATE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def timeline_capture_blocks() -> bool:
    """Whether timeline incoherence marks CORRUPT at segment close."""
    if timeline_gate_enabled():
        return True
    return os.environ.get("EGO_TIMELINE_CAPTURE_BLOCK", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def timeline_upload_qc_enabled() -> bool:
    """Upload path rejects segments whose timeline exceeds EGO_TIMELINE_MAX_DEV."""
    return os.environ.get("EGO_TIMELINE_UPLOAD_QC", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _timeline_max_dev() -> float:
    return float(os.environ.get("EGO_TIMELINE_MAX_DEV", "0.03"))


def timeline_stats_max_dev() -> float:
    """Stress/soak reporting tier only — does not relax corpus or upload QC."""
    return float(os.environ.get("EGO_TIMELINE_STATS_MAX_DEV", "0.05"))


def _timeline_min_frames() -> int:
    return max(1, int(os.environ.get("EGO_TIMELINE_MIN_FRAMES", "300")))


def _timeline_min_span_s() -> float:
    return float(os.environ.get("EGO_TIMELINE_MIN_SPAN_S", "10.0"))


def timestamps_uniform(ts_ns: list[int], *, tolerance_ms: float = 0.5) -> bool:
    """True when consecutive timestamps follow a fixed grid within tolerance."""
    if len(ts_ns) < 2:
        return True
    dts_ms = [(int(ts_ns[i]) - int(ts_ns[i - 1])) / 1e6 for i in range(1, len(ts_ns))]
    mean_ms = sum(dts_ms) / len(dts_ms)
    return all(abs(d - mean_ms) <= tolerance_ms for d in dts_ms)


def _record_span_from_bounds(
    *,
    device_min_ns: int | None,
    device_max_ns: int | None,
    grid_min_ns: int | None,
    grid_max_ns: int | None,
    grid_ns: list[int] | None = None,
) -> tuple[int | None, int | None, str]:
    """Pick QC record span: device time preferred, uniform grid as legacy fallback."""
    if (
        device_min_ns is not None
        and device_max_ns is not None
        and int(device_max_ns) > int(device_min_ns)
    ):
        return int(device_min_ns), int(device_max_ns), "device"
    if grid_ns is not None and len(grid_ns) >= 2 and timestamps_uniform(grid_ns):
        return int(min(grid_ns)), int(max(grid_ns)), "grid_uniform"
    if grid_min_ns is not None and grid_max_ns is not None and int(grid_max_ns) > int(grid_min_ns):
        return int(grid_min_ns), int(grid_max_ns), "grid"
    return None, None, "missing"


def timeline_grace_applies(timeline: dict[str, Any], *, frame_count: int) -> bool:
    """Short tail segments (e.g. stop mid-segment) have noisy ratio — do not reject."""
    if frame_count < _timeline_min_frames():
        return True
    min_span = _timeline_min_span_s()
    if float(timeline.get("imu_span_s") or 0) < min_span:
        return True
    record_span = float(
        timeline.get("device_span_s")
        or timeline.get("record_span_s")
        or timeline.get("grid_span_s")
        or 0
    )
    if record_span < min_span:
        return True
    return False


def strict_fps_gate_enabled() -> bool:
    return os.environ.get("EGO_STRICT_FPS_GATE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def strict_fps_gate_fast_path_enabled() -> bool:
    """Skip full MCAP re-read on segment close when writer-span timeline is present."""
    return os.environ.get("EGO_STRICT_FPS_GATE_FAST", "1").strip().lower() in (
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
    lo, hi = _eff_hz_band(interval_ms=interval_ms)
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
    lo, hi = _eff_hz_band(interval_ms=interval_ms)
    eff_hz_ok = lo <= report.get("eff_hz", 0) <= hi
    return bool(band_ok and dt_ok and eff_hz_ok)


def _read_mcap_series(mcap_path: Path) -> tuple[list[int], list[int], list[int]]:
    """Read grid timestamps, optional device timestamps, and IMU device timestamps."""
    from mcap.reader import make_reader

    grid_out: list[int] = []
    device_out: list[int] = []
    imu_out: list[int] = []
    with open(mcap_path, "rb") as fp:
        reader = make_reader(fp)
        for _schema, channel, message in reader.iter_messages():
            if channel.topic == TOPIC_IMU_RAW:
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
            dev_ts = payload.get("primary_device_timestamp_ns")
            if dev_ts is not None:
                device_out.append(int(dev_ts))
    return grid_out, device_out, imu_out


def _read_mcap_obs_timestamps(mcap_path: Path) -> list[int]:
    grid_ns, device_ns, _imu_ns = _read_mcap_series(mcap_path)
    return device_ns if len(device_ns) >= 2 else grid_ns


def timeline_from_writer_spans(
    *,
    frame_count: int,
    grid_min_ns: int | None,
    grid_max_ns: int | None,
    imu_min_ns: int | None,
    imu_max_ns: int | None,
    imu_samples: int,
    device_min_ns: int | None = None,
    device_max_ns: int | None = None,
) -> dict[str, Any]:
    """O(1) timeline coherence from writer min/max spans (no MCAP re-read)."""
    if frame_count < 2:
        return {"error": "too_few_frames", "grid_frames": frame_count, "source": "writer_spans"}
    if imu_min_ns is None or imu_max_ns is None or imu_samples < 2:
        return {
            "error": "no_imu_reference",
            "imu_samples": imu_samples,
            "source": "writer_spans",
        }
    record_min, record_max, record_source = _record_span_from_bounds(
        device_min_ns=device_min_ns,
        device_max_ns=device_max_ns,
        grid_min_ns=grid_min_ns,
        grid_max_ns=grid_max_ns,
    )
    if record_min is None or record_max is None:
        return {
            "error": "missing_record_span",
            "grid_frames": frame_count,
            "source": "writer_spans",
        }
    grid_span_s = (
        (int(grid_max_ns) - int(grid_min_ns)) / 1e9
        if grid_min_ns is not None and grid_max_ns is not None and int(grid_max_ns) > int(grid_min_ns)
        else None
    )
    device_span_s = (
        (int(device_max_ns) - int(device_min_ns)) / 1e9
        if device_min_ns is not None and device_max_ns is not None and int(device_max_ns) > int(device_min_ns)
        else None
    )
    record_span_s = (int(record_max) - int(record_min)) / 1e9
    imu_span_s = (int(imu_max_ns) - int(imu_min_ns)) / 1e9
    if record_span_s <= 0 or imu_span_s <= 0:
        return {
            "error": "degenerate_span",
            "record_span_s": record_span_s,
            "device_span_s": device_span_s,
            "grid_span_s": grid_span_s,
            "imu_span_s": imu_span_s,
            "source": "writer_spans",
        }
    ratio = imu_span_s / record_span_s
    max_dev = _timeline_max_dev()
    return {
        "source": "writer_spans",
        "record_source": record_source,
        "grid_frames": frame_count,
        "grid_span_s": grid_span_s,
        "device_span_s": device_span_s,
        "record_span_s": record_span_s,
        "imu_samples": imu_samples,
        "imu_span_s": imu_span_s,
        "real_fps": frame_count / imu_span_s,
        "claimed_fps": frame_count / record_span_s,
        "ratio": ratio,
        "max_dev": max_dev,
        "ok": abs(ratio - 1.0) <= max_dev,
    }


def timeline_ratio_ok(timeline: dict[str, Any], *, max_dev: float | None = None) -> bool:
    if timeline.get("error"):
        return False
    ratio = timeline.get("ratio")
    if ratio is None:
        return bool(timeline.get("ok"))
    dev = _timeline_max_dev() if max_dev is None else max_dev
    return abs(float(ratio) - 1.0) <= dev


def timeline_integrity_issues(
    timeline: dict[str, Any],
    *,
    frame_count: int | None = None,
    max_dev: float | None = None,
) -> list[str]:
    """Timeline QC issues (upload/corpus tier uses EGO_TIMELINE_MAX_DEV by default)."""
    if not timeline:
        return []
    if frame_count is not None and timeline_grace_applies(timeline, frame_count=frame_count):
        return []
    if timeline.get("error"):
        return [f"timeline_unavailable:{timeline['error']}"]
    if timeline_ratio_ok(timeline, max_dev=max_dev):
        return []
    return [
        "timeline_incoherent:"
        f"ratio={timeline.get('ratio', 0):.4f},"
        f"real_fps={timeline.get('real_fps', 0):.3f},"
        f"claimed_fps={timeline.get('claimed_fps', 0):.3f}",
    ]


def timeline_stats_issues(
    timeline: dict[str, Any],
    *,
    frame_count: int | None = None,
) -> list[str]:
    """Stress/soak stats tier (EGO_TIMELINE_STATS_MAX_DEV, default 5%)."""
    return timeline_integrity_issues(
        timeline,
        frame_count=frame_count,
        max_dev=timeline_stats_max_dev(),
    )


def log_timeline_verdict(
    segment_name: str,
    timeline: dict[str, Any],
    *,
    frame_count: int | None = None,
) -> None:
    if timeline.get("error"):
        print(f"[timeline] {segment_name}: unavailable ({timeline['error']})", flush=True)
        return
    if frame_count is not None and timeline_grace_applies(timeline, frame_count=frame_count):
        print(
            f"[timeline] {segment_name}: grace frames={frame_count} "
            f"ratio={timeline.get('ratio', 0):.4f} "
            f"real_span={timeline.get('imu_span_s', 0):.2f}s",
            flush=True,
        )
        return
    verdict = "ok" if timeline.get("ok") else "COMPRESSED"
    record_span = timeline.get("record_span_s", timeline.get("device_span_s", timeline.get("grid_span_s", 0)))
    print(
        f"[timeline] {segment_name}: {verdict} ratio={timeline.get('ratio', 0):.4f} "
        f"real_fps={timeline.get('real_fps', 0):.3f} claimed_fps={timeline.get('claimed_fps', 0):.3f} "
        f"real_span={timeline.get('imu_span_s', 0):.2f}s "
        f"recorded_span={record_span:.2f}s "
        f"record_source={timeline.get('record_source', timeline.get('source', 'unknown'))} "
        f"source={timeline.get('source', 'unknown')}",
        flush=True,
    )


def analyze_timeline_coherence(
    grid_ns: list[int],
    imu_ns: list[int],
    device_ns: list[int] | None = None,
) -> dict[str, Any]:
    """Compare device (preferred) or verified-uniform grid span against IMU wall clock."""
    device_ns = list(device_ns or [])
    if len(imu_ns) < 2:
        return {"error": "no_imu_reference", "imu_samples": len(imu_ns)}

    record_min, record_max, record_source = _record_span_from_bounds(
        device_min_ns=min(device_ns) if len(device_ns) >= 2 else None,
        device_max_ns=max(device_ns) if len(device_ns) >= 2 else None,
        grid_min_ns=min(grid_ns) if len(grid_ns) >= 2 else None,
        grid_max_ns=max(grid_ns) if len(grid_ns) >= 2 else None,
        grid_ns=grid_ns,
    )
    if record_min is None or record_max is None:
        if len(grid_ns) >= 2 and not timestamps_uniform(grid_ns):
            return {
                "error": "non_uniform_grid_without_device",
                "grid_frames": len(grid_ns),
            }
        return {"error": "too_few_record_timestamps", "grid_frames": len(grid_ns)}

    grid_span_s = (max(grid_ns) - min(grid_ns)) / 1e9 if len(grid_ns) >= 2 else None
    device_span_s = (max(device_ns) - min(device_ns)) / 1e9 if len(device_ns) >= 2 else None
    record_span_s = (int(record_max) - int(record_min)) / 1e9
    imu_span_s = (max(imu_ns) - min(imu_ns)) / 1e9
    if record_span_s <= 0 or imu_span_s <= 0:
        return {
            "error": "degenerate_span",
            "record_span_s": record_span_s,
            "device_span_s": device_span_s,
            "grid_span_s": grid_span_s,
            "imu_span_s": imu_span_s,
        }
    ratio = imu_span_s / record_span_s
    max_dev = _timeline_max_dev()
    return {
        "grid_frames": len(grid_ns) if grid_ns else len(device_ns),
        "grid_span_s": grid_span_s,
        "device_span_s": device_span_s,
        "record_span_s": record_span_s,
        "record_source": record_source,
        "imu_samples": len(imu_ns),
        "imu_span_s": imu_span_s,
        "real_fps": (len(device_ns) if len(device_ns) >= 2 else len(grid_ns)) / imu_span_s,
        "claimed_fps": (len(device_ns) if len(device_ns) >= 2 else len(grid_ns)) / record_span_s,
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
    interval_ms: float | None = None,
) -> tuple[bool, list[str]]:
    if not strict_fps_gate_enabled() and not timeline_gate_enabled():
        return True, []
    if interval_ms is None:
        interval_ms = capture_interval_ms()
    if not mcap_path.is_file():
        return False, ["missing_segment_mcap"]
    issues: list[str] = []
    try:
        grid_ns, device_ns, imu_ns = _read_mcap_series(mcap_path)
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
        timeline = analyze_timeline_coherence(grid_ns, imu_ns, device_ns)
        if timeline.get("error"):
            print(f"[timeline] {mcap_path.name}: unavailable ({timeline['error']})", flush=True)
        else:
            verdict = "ok" if timeline["ok"] else "COMPRESSED"
            record_span = timeline.get(
                "record_span_s", timeline.get("device_span_s", timeline.get("grid_span_s", 0))
            )
            print(
                f"[timeline] {mcap_path.name}: {verdict} ratio={timeline['ratio']:.4f} "
                f"real_fps={timeline['real_fps']:.3f} claimed_fps={timeline['claimed_fps']:.3f} "
                f"real_span={timeline['imu_span_s']:.2f}s recorded_span={record_span:.2f}s "
                f"record_source={timeline.get('record_source', 'unknown')}",
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
    grid_ns, device_ns, imu_ns = _read_mcap_series(mcap_path)
    report = analyze_timeline_coherence(grid_ns, imu_ns, device_ns)
    report["path"] = str(mcap_path)
    return report


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        for path in sorted(Path().glob(arg)) if any(c in arg for c in "*?[") else [Path(arg)]:
            print(json.dumps(audit_timeline(path)))
