"""Tests for the FSYNC GPIO script generation and the timeline coherence check.

The generated script runs on the OAK's LEON core, not here, so these tests cover what
is checkable off-device: that both variants are syntactically valid Python after the
fps substitution, that the legacy variant is preserved verbatim for rollback, and that
the self-correcting variant contains no fixed overhead subtraction.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
from pathlib import Path

import pytest

CLIENT_DIR = Path(__file__).resolve().parents[1]
SOURCE = (CLIENT_DIR / "oak_4p_capture.py").read_text()


def _extract(name: str) -> str:
    match = re.search(name + r' = """(.*?)"""', SOURCE, re.S)
    assert match is not None, f"{name} not found in oak_4p_capture.py"
    return match.group(1)


HEAD = _extract("_FSYNC_GPIO_HEAD")
LOOP_LEGACY = _extract("_FSYNC_LOOP_LEGACY")
LOOP_SELFCORRECT = _extract("_FSYNC_LOOP_SELFCORRECT")


@pytest.mark.parametrize("loop", [LOOP_LEGACY, LOOP_SELFCORRECT], ids=["legacy", "selfcorrect"])
@pytest.mark.parametrize("fps", [24.0, 30.0, 60.0])
def test_generated_script_is_valid_python(loop: str, fps: float) -> None:
    ast.parse((HEAD + loop) % fps)


@pytest.mark.parametrize("loop", [LOOP_LEGACY, LOOP_SELFCORRECT], ids=["legacy", "selfcorrect"])
def test_loop_bodies_carry_no_format_placeholder(loop: str) -> None:
    # Only the head may contain %f; a stray % in a loop body would corrupt the
    # substitution or raise at pipeline build time.
    assert "%" not in loop


def test_head_has_exactly_one_placeholder() -> None:
    assert HEAD.count("%") == 1
    assert "%f" in HEAD


def test_legacy_loop_preserves_the_original_timing() -> None:
    # This is the rollback path; its behaviour must stay bit-for-bit what production
    # ran at 26.1Hz, so that OAK_FSYNC_FIX=0 is a true revert.
    assert "overhead = 0.003" in LOOP_LEGACY
    assert "time.sleep(period - active - overhead)" in LOOP_LEGACY


def test_selfcorrect_loop_uses_absolute_deadlines() -> None:
    assert "overhead" not in LOOP_SELFCORRECT
    assert "next_t = next_t + period" in LOOP_SELFCORRECT
    # Must re-anchor rather than chase a backlog after an overrun.
    assert "next_t = clock()" in LOOP_SELFCORRECT


def test_selfcorrect_loop_tolerates_missing_monotonic() -> None:
    # The LEON Script runtime is not guaranteed to expose time.monotonic.
    assert "except AttributeError" in LOOP_SELFCORRECT
    assert "clock = time.time" in LOOP_SELFCORRECT


PERIOD = 1.0 / 30.0
ACTIVE = 0.001
# The two GPIO.write calls cost ~8ms in total on the 130 machine under 4x H.264; that
# is the real figure the hardcoded 3ms was wrong about.
WRITE_COST = 0.004
LEGACY_OVERHEAD = 0.003


def _simulate_legacy(pulses: int, write_cost: float = WRITE_COST) -> float:
    now = 0.0
    stamps = []
    for _ in range(pulses):
        stamps.append(now)
        now += write_cost  # GPIO.write(1)
        now += ACTIVE  # time.sleep(active)
        now += write_cost  # GPIO.write(0)
        now += PERIOD - ACTIVE - LEGACY_OVERHEAD
    return (pulses - 1) / (stamps[-1] - stamps[0])


def _simulate_selfcorrect(pulses: int, write_cost: float = WRITE_COST) -> float:
    now = 0.0
    next_t = 0.0
    stamps = []
    for _ in range(pulses):
        stamps.append(now)
        now += write_cost
        now += ACTIVE
        now += write_cost
        next_t += PERIOD
        delay = next_t - now
        if delay > 0:
            now = next_t
        else:
            next_t = now
    return (pulses - 1) / (stamps[-1] - stamps[0])


def test_legacy_loop_reproduces_the_observed_26hz_defect() -> None:
    # Unaccounted overhead lands directly on the period: 33.33 + (8 - 3) = 38.33ms.
    assert _simulate_legacy(300) == pytest.approx(26.09, abs=0.1)


def test_selfcorrect_loop_holds_rate_under_the_same_latency() -> None:
    assert _simulate_selfcorrect(300) == pytest.approx(30.0, abs=0.01)


@pytest.mark.parametrize("write_cost", [0.0, 0.002, 0.004, 0.008, 0.014])
def test_selfcorrect_rate_is_independent_of_overhead(write_cost: float) -> None:
    # The whole point: rate must not depend on how long the writes take, as long as
    # the work fits inside a period.
    assert _simulate_selfcorrect(300, write_cost) == pytest.approx(30.0, abs=0.01)


def test_selfcorrect_degrades_gracefully_when_work_exceeds_the_period() -> None:
    # 20ms per write cannot fit a 33.33ms period; the loop must settle at whatever the
    # hardware allows instead of accumulating an unbounded deadline backlog.
    hz = _simulate_selfcorrect(300, write_cost=0.020)
    assert 0 < hz < 30.0
    assert hz == pytest.approx(1.0 / (2 * 0.020 + ACTIVE), rel=0.02)


@pytest.fixture()
def gate():
    sys.path.insert(0, str(CLIENT_DIR))
    try:
        module = importlib.import_module("strict_fps_gate")
        yield importlib.reload(module)
    finally:
        sys.path.remove(str(CLIENT_DIR))


def _series(frames: int, grid_interval_ms: float, real_span_s: float):
    grid = [int(i * grid_interval_ms * 1e6) for i in range(frames)]
    imu = [int(i * real_span_s / 400 * 1e9) for i in range(401)]
    return grid, imu


def test_timeline_detects_the_production_compression(gate) -> None:
    # The real numbers from seg_000022: 1800 frames stamped 33ms apart, 68.58s elapsed.
    grid, imu = _series(1800, 33.0, 68.58)
    report = gate.analyze_timeline_coherence(grid, imu)
    assert report["ratio"] == pytest.approx(1.155, abs=0.01)
    assert report["real_fps"] == pytest.approx(26.25, abs=0.1)
    assert report["claimed_fps"] == pytest.approx(30.32, abs=0.1)
    assert report["ok"] is False


def test_timeline_accepts_a_healthy_segment(gate) -> None:
    # 1800 frames on a 33ms grid genuinely take 59.37s.
    grid, imu = _series(1800, 33.0, 59.37)
    report = gate.analyze_timeline_coherence(grid, imu)
    assert report["ratio"] == pytest.approx(1.0, abs=0.001)
    assert report["ok"] is True


def test_timeline_is_blind_without_an_imu_reference(gate) -> None:
    grid, _ = _series(1800, 33.0, 68.58)
    report = gate.analyze_timeline_coherence(grid, [])
    assert report["error"] == "no_imu_reference"


def test_strict_fps_gate_cannot_see_compression(gate) -> None:
    """Documents why the timeline check had to be added.

    A 26fps and a 30fps capture produce identical grid spacing, so the existing gate
    passes both. This is the blind spot, asserted so it cannot be silently reintroduced.
    """
    grid, _ = _series(1800, 33.0, 68.58)
    report = gate.analyze_timestamp_series(grid)
    assert report["dt_mean_ms"] == pytest.approx(33.0)
    assert report["dt_std_ms"] == pytest.approx(0.0)
    assert gate.strict_timestamp_series_ok(report) is True


def test_timeline_gate_is_report_only_by_default(gate, monkeypatch) -> None:
    monkeypatch.delenv("EGO_TIMELINE_GATE", raising=False)
    assert gate.timeline_gate_enabled() is False
    monkeypatch.setenv("EGO_TIMELINE_GATE", "1")
    assert gate.timeline_gate_enabled() is True


def test_unreadable_segment_is_reported_not_raised(gate, monkeypatch, tmp_path) -> None:
    """A truncated segment must not take the capture process down.

    Reproduces the ego-001 restart loop: the reader raises while parsing a truncated
    segment's footer, and because this runs during integrity checking the process dies
    and restarts onto the same file.
    """
    from mcap.exceptions import RecordLengthLimitExceeded

    mcap_path = tmp_path / "segment.mcap"
    mcap_path.write_bytes(b"\x89MCAP0\r\n" + b"\x00" * 32)

    def _boom(_path):
        raise RecordLengthLimitExceeded(37, 1894776640760458017, 4294967296)

    monkeypatch.setattr(gate, "_read_mcap_series", _boom)
    monkeypatch.setenv("EGO_STRICT_FPS_GATE", "1")

    ok, issues = gate.check_mcap_strict_fps(mcap_path, 1800)
    assert ok is False
    assert issues == ["mcap_unreadable:RecordLengthLimitExceeded"]
