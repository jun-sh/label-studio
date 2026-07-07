#!/usr/bin/env python3
"""EGO capture startup baseline benchmark — 5 start/stop cycles, 5-segment timing.

Run on 214 (no code changes required):
  python3 capture-startup-benchmark.py --cycles 5 --scenario connected

Outputs JSON + markdown report under /tmp/ego-capture-benchmark-<ts>/
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

CAPTURE_TARGET = "ecs-oak-capture-stack.target"
CAPTURE_UNIT = "ecs-record-oak-stream.service"
DEFAULT_WEB = "http://127.0.0.1:8080"
POLL_S = 0.2
RECORD_HOLD_S = 4.0
STOP_WAIT_S = 120.0
MIN_GAP_S = 4.0
RECORDING_TIMEOUT_S = 45.0


@dataclass
class CycleTimings:
    cycle: int
    t_click_start: float = 0.0
    t_starting: float | None = None
    t_warming: float | None = None
    t_recording: float | None = None
    t_click_stop: float | None = None
    t_idle: float | None = None
    # journal-derived (epoch from log parse, monotonic offset applied)
    t_proc_start: float | None = None
    t_oak_resolution_ok: float | None = None
    t_heartbeat_ok: float | None = None
    t_heartbeat_first_retry: float | None = None
    t_capture_only_log: float | None = None
    heartbeat_retries: int = 0
    probe_duration_log: float | None = None
    errors: list[str] = field(default_factory=list)

    def seg_starting_s(self) -> float | None:
        if self.t_starting is None:
            return None
        return self.t_starting - self.t_click_start

    def seg_warming_pre_s(self) -> float | None:
        if self.t_proc_start is None or self.t_oak_resolution_ok is None:
            return None
        return self.t_oak_resolution_ok - self.t_proc_start

    def seg_heartbeat_s(self) -> float | None:
        if self.t_oak_resolution_ok is None:
            return None
        end = self.t_heartbeat_ok or self.t_capture_only_log
        if end is None:
            return None
        return end - self.t_oak_resolution_ok

    def seg_probe_s(self) -> float | None:
        if self.probe_duration_log is not None:
            return self.probe_duration_log
        if self.t_heartbeat_ok is None or self.t_capture_only_log is None:
            if self.t_oak_resolution_ok is not None and self.t_capture_only_log is not None:
                return self.t_capture_only_log - self.t_oak_resolution_ok
            return None
        return self.t_capture_only_log - self.t_heartbeat_ok

    def seg_ui_lag_s(self) -> float | None:
        if self.t_capture_only_log is None or self.t_recording is None:
            return None
        return max(0.0, self.t_recording - self.t_capture_only_log)

    def total_to_recording_s(self) -> float | None:
        if self.t_recording is None:
            return None
        return self.t_recording - self.t_click_start


def _http_json(url: str, method: str = "GET", timeout: float = 60.0) -> dict[str, Any]:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_capture_active() -> bool:
    proc = subprocess.run(
        ["systemctl", "--user", "is-active", CAPTURE_TARGET],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return proc.stdout.strip() == "active"


def _poll_status(base: str) -> dict[str, Any]:
    return _http_json(f"{base}/api/status")


def _wait_state(
    base: str,
    want: str,
    *,
    since: float,
    timeout: float,
) -> tuple[float | None, dict[str, Any] | None]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            last = _poll_status(base)
            if last.get("state") == want:
                return time.monotonic(), last
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(POLL_S)
    return None, last


def _journal_since(cycle_start_wall: float) -> str:
    since = datetime.fromtimestamp(cycle_start_wall).strftime("%Y-%m-%d %H:%M:%S")
    proc = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            CAPTURE_UNIT,
            f"--since={since}",
            "--no-pager",
            "-o",
            "short-iso-precise",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else ""


def _parse_journal(lines: str, t0_mono: float, cycle: CycleTimings) -> None:
    """Map journal lines to monotonic timestamps relative to t_click_start."""
    patterns = [
        ("t_proc_start", re.compile(r"oak_pipeline=")),
        ("t_oak_resolution_ok", re.compile(r"oak_output_resolution ok")),
        ("t_heartbeat_ok", re.compile(r"heartbeat_ok")),
        ("t_heartbeat_first_retry", re.compile(r"heartbeat_retry")),
        ("t_capture_only_log", re.compile(r"capture-only session=")),
        ("probe_log", re.compile(r"probe_duration_s=([0-9.]+)")),
    ]
    retries = 0
    for line in lines.splitlines():
        # short-iso-precise: 2026-07-05T16:30:01.123456+0800 host ...
        m_ts = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)", line)
        if not m_ts:
            continue
        try:
            raw = m_ts.group(1)
            # parse without tz for relative ordering within same machine
            dt = datetime.strptime(raw[:26], "%Y-%m-%dT%H:%M:%S.%f")
            wall = dt.timestamp()
        except ValueError:
            continue
        mono = t0_mono + (wall - cycle.t_click_start) if cycle.t_click_start else wall

        if "heartbeat_retry" in line:
            retries += 1

        if "oak_pipeline=" in line and cycle.t_proc_start is None:
            cycle.t_proc_start = mono
        if "oak_output_resolution ok" in line and cycle.t_oak_resolution_ok is None:
            cycle.t_oak_resolution_ok = mono
        if "heartbeat_ok" in line and cycle.t_heartbeat_ok is None:
            cycle.t_heartbeat_ok = mono
        if "heartbeat_retry" in line and cycle.t_heartbeat_first_retry is None:
            cycle.t_heartbeat_first_retry = mono
        if "capture-only session=" in line and cycle.t_capture_only_log is None:
            cycle.t_capture_only_log = mono
        pm = re.search(r"probe_duration_s=([0-9.]+)", line)
        if pm and cycle.probe_duration_log is None:
            cycle.probe_duration_log = float(pm.group(1))

    cycle.heartbeat_retries = retries


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    vals = sorted(values)
    n = len(vals)
    p95_idx = min(n - 1, int(n * 0.95))
    return {
        "mean": round(statistics.mean(vals), 3),
        "min": round(vals[0], 3),
        "max": round(vals[-1], 3),
        "p95": round(vals[p95_idx], 3),
    }


def run_cycle(base: str, cycle_num: int) -> CycleTimings:
    c = CycleTimings(cycle=cycle_num)
    if _is_capture_active():
        c.errors.append("capture already active; stopping first")
        _http_json(f"{base}/api/capture/stop", method="POST", timeout=STOP_WAIT_S)
        _wait_state(base, "idle", since=time.monotonic(), timeout=STOP_WAIT_S)
        time.sleep(MIN_GAP_S)

    # Ensure stack fully stopped before next start (avoids cycle-1 false timeouts).
    idle_deadline = time.monotonic() + 30.0
    while time.monotonic() < idle_deadline:
        if not _is_capture_active():
            try:
                st = _poll_status(base).get("state")
                if st == "idle":
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                pass
        time.sleep(0.3)
    time.sleep(1.0)

    c.t_click_start = time.monotonic()
    wall_start = time.time()
    try:
        _http_json(f"{base}/api/capture/start", method="POST", timeout=60.0)
    except Exception as exc:
        c.errors.append(f"start POST failed: {exc}")
        return c

    t, _ = _wait_state(base, "starting", since=c.t_click_start, timeout=5.0)
    c.t_starting = t or c.t_click_start

    t, _ = _wait_state(base, "warming", since=c.t_click_start, timeout=60.0)
    c.t_warming = t

    t, _ = _wait_state(base, "recording", since=c.t_click_start, timeout=RECORDING_TIMEOUT_S)
    c.t_recording = t
    if t is None:
        c.errors.append("timeout waiting for recording state")
        journal = _journal_since(wall_start - 1.0)
        if "SyntaxError" in journal or "FAILURE" in journal:
            c.errors.append("capture process crash — check journalctl ecs-record-oak-stream")
        _parse_journal(journal, c.t_click_start, c)
        try:
            _http_json(f"{base}/api/capture/stop", method="POST", timeout=STOP_WAIT_S)
        except Exception:
            pass
        time.sleep(MIN_GAP_S)
        return c

    time.sleep(RECORD_HOLD_S)

    c.t_click_stop = time.monotonic()
    try:
        _http_json(f"{base}/api/capture/stop", method="POST", timeout=STOP_WAIT_S)
    except Exception as exc:
        c.errors.append(f"stop POST failed: {exc}")

    t, _ = _wait_state(base, "idle", since=c.t_click_stop, timeout=STOP_WAIT_S)
    c.t_idle = t

    journal = _journal_since(wall_start - 1.0)
    _parse_journal(journal, c.t_click_start, c)

    time.sleep(MIN_GAP_S)
    return c


def build_report(cycles: list[CycleTimings], scenario: str, web_base: str) -> str:
    lines = [
        "# EGO Capture Startup Baseline Report",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Scenario: {scenario}",
        f"- Web API: {web_base}",
        f"- Cycles: {len(cycles)}",
        "",
        "## Per-cycle timings (seconds)",
        "",
        "| Cycle | starting | warming_pre | heartbeat | probe | ui_lag | **total→recording** | retries |",
        "|-------|----------|-------------|-----------|-------|--------|---------------------|---------|",
    ]
    for c in cycles:
        lines.append(
            f"| {c.cycle} | "
            f"{c.seg_starting_s()!s} | {c.seg_warming_pre_s()!s} | {c.seg_heartbeat_s()!s} | "
            f"{c.seg_probe_s()!s} | {c.seg_ui_lag_s()!s} | **{c.total_to_recording_s()!s}** | "
            f"{c.heartbeat_retries} |"
        )
        if c.errors:
            lines.append(f"| | errors: {', '.join(c.errors)} | | | | | | |")

    segments = {
        "starting": [c.seg_starting_s() for c in cycles if c.seg_starting_s() is not None],
        "warming_pre": [c.seg_warming_pre_s() for c in cycles if c.seg_warming_pre_s() is not None],
        "heartbeat": [c.seg_heartbeat_s() for c in cycles if c.seg_heartbeat_s() is not None],
        "probe": [c.seg_probe_s() for c in cycles if c.seg_probe_s() is not None],
        "ui_lag": [c.seg_ui_lag_s() for c in cycles if c.seg_ui_lag_s() is not None],
        "total": [c.total_to_recording_s() for c in cycles if c.total_to_recording_s() is not None],
    }

    lines.extend(["", "## Aggregate (mean / p95 / max)", ""])
    for name, vals in segments.items():
        st = _stats(vals)
        if st:
            lines.append(
                f"- **{name}**: mean={st['mean']}s p95={st['p95']}s max={st['max']}s (n={len(vals)})"
            )

    # bottleneck estimate
    means: dict[str, float] = {}
    for name in ("starting", "warming_pre", "heartbeat", "probe", "ui_lag"):
        vals = segments.get(name, [])
        if vals:
            means[name] = statistics.mean(vals)
    if means:
        top = max(means, key=means.get)  # type: ignore[arg-type]
        lines.extend(
            [
                "",
                "## TOP1 bottleneck (by mean segment duration)",
                "",
                f"**{top}** — mean {means[top]:.3f}s",
                "",
                "### Log keywords",
                "",
                "| Symptom | Keywords |",
                "|---------|----------|",
                "| OAK cold start slow | gap before `oak_pipeline=` |",
                "| Resolution verify slow | gap `oak_pipeline` → `oak_output_resolution ok` (up to 8s) |",
                "| 34 heartbeat blocking | `heartbeat_retry`, long gap before `capture-only` |",
                "| Fixed probe | ~3s between heartbeat and `capture-only` |",
                "| UI poll lag | `capture-only` in journal but recording state delayed ~0.2-0.8s |",
                "| Stable slow | consistent totals, no `heartbeat_retry` |",
                "| Spiky stall | `heartbeat_retry` or resolution timeout errors |",
            ]
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="EGO capture startup benchmark")
    p.add_argument("--web", default=DEFAULT_WEB, help="ego-web base URL")
    p.add_argument("--cycles", type=int, default=5)
    p.add_argument("--scenario", default="connected", choices=["connected", "disconnected"])
    p.add_argument("--out-dir", type=str, default="")
    args = p.parse_args()

    out = Path(args.out_dir or f"/tmp/ego-capture-benchmark-{int(time.time())}")
    out.mkdir(parents=True, exist_ok=True)

    print(f"Benchmark: {args.cycles} cycles, scenario={args.scenario}, web={args.web}", flush=True)
    if args.scenario == "disconnected":
        print(
            "NOTE: disconnected scenario requires 34 blocked before run; "
            "measuring heartbeat stall manually.",
            flush=True,
        )

    cycles: list[CycleTimings] = []
    for i in range(1, args.cycles + 1):
        print(f"--- cycle {i}/{args.cycles} ---", flush=True)
        c = run_cycle(args.web, i)
        cycles.append(c)
        print(
            f"  total→recording={c.total_to_recording_s()}s "
            f"probe={c.seg_probe_s()}s ui_lag={c.seg_ui_lag_s()}s",
            flush=True,
        )

    data = [asdict(c) for c in cycles]
    (out / "cycles.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    report = build_report(cycles, args.scenario, args.web)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report, flush=True)
    print(f"Written: {out}/report.md", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
