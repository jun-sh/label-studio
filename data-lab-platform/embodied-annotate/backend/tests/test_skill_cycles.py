"""Tests for per-cycle merge and box_cycle sync."""

from __future__ import annotations

from skill_derivation import (
    count_successful_cycles,
    derive_skill_segments,
    merge_skill_cycles,
    sync_box_cycle_from_cycles,
)


def test_merge_preserves_success_when_structure_unchanged() -> None:
    derived = [
        {
            "cycle_id": 0,
            "start": 0.0,
            "end": 10.0,
            "complete": True,
            "success": True,
            "fail_reason": "none",
            "success_source": "auto",
        }
    ]
    previous = [
        {
            "cycle_id": 0,
            "start": 0.0,
            "end": 10.0,
            "complete": True,
            "success": False,
            "fail_reason": "slip_grasp",
            "success_source": "manual",
        }
    ]
    merged = merge_skill_cycles(previous, derived)
    assert merged[0]["success"] is False
    assert merged[0]["fail_reason"] == "slip_grasp"


def test_merge_auto_baseline_when_structure_changes() -> None:
    derived = [
        {
            "cycle_id": 0,
            "start": 0.0,
            "end": 12.0,
            "complete": True,
            "success": None,
            "fail_reason": "none",
        }
    ]
    previous = [
        {
            "cycle_id": 0,
            "start": 0.0,
            "end": 10.0,
            "complete": True,
            "success": True,
            "fail_reason": "none",
        }
    ]
    merged = merge_skill_cycles(previous, derived)
    assert merged[0]["success"] is True
    assert merged[0]["success_source"] == "auto"


def test_auto_cycle_defaults_complete() -> None:
    from skill_derivation import auto_cycle_defaults

    out = auto_cycle_defaults({"cycle_id": 0, "complete": True})
    assert out["success"] is True
    assert out["fail_reason"] == "none"


def test_sync_box_cycle_from_cycles() -> None:
    cycles = [
        {"cycle_id": 0, "success": True},
        {"cycle_id": 1, "success": False},
        {"cycle_id": 2, "success": True},
    ]
    fields = sync_box_cycle_from_cycles(cycles, {})
    assert fields["box_cycle"] == 2
    assert count_successful_cycles(cycles) == 2


def test_derive_cycles_include_time_bounds() -> None:
    subtasks = [
        {"start": 1.0, "end": 2.0, "label": "reach"},
        {"start": 2.0, "end": 3.0, "label": "pre_grasp"},
        {"start": 3.0, "end": 4.0, "label": "contact"},
        {"start": 4.0, "end": 5.0, "label": "lift"},
        {"start": 5.0, "end": 6.0, "label": "transport"},
        {"start": 6.0, "end": 7.0, "label": "place"},
        {"start": 7.0, "end": 8.0, "label": "release"},
    ]
    result = derive_skill_segments(subtasks, {"skill_derivation": {"enabled": True}})
    assert result["cycles"][0]["start"] == 1.0
    assert result["cycles"][0]["end"] == 8.0
    assert result["cycles"][0]["success"] is True
    assert result["skill_segments"][0].get("subgoal")
