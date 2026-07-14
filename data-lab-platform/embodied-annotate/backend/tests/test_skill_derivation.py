"""Tests for L1 skill derivation from L2 subtasks."""

from __future__ import annotations

from skill_derivation import (
    DEFAULT_SKILL_DERIVATION,
    derive_skill_segments,
    split_subtasks_into_cycles,
)


def _one_box_cycle(start: float = 0.0, step: float = 2.0) -> list[dict]:
    labels = ["reach", "pre_grasp", "contact", "lift", "transport", "place", "release"]
    segs = []
    t = start
    for label in labels:
        segs.append({"start": t, "end": t + step, "label": label})
        t += step
    return segs


def test_split_two_cycles() -> None:
    cycle1 = _one_box_cycle(0.0)
    cycle2 = _one_box_cycle(20.0)
    idle = {"start": 14.0, "end": 20.0, "label": "idle"}
    subtasks = cycle1 + [idle] + cycle2
    cycles = split_subtasks_into_cycles(subtasks)
    assert len(cycles) == 2


def test_derive_three_skills_per_cycle() -> None:
    subtasks = _one_box_cycle()
    schema = {"skill_derivation": DEFAULT_SKILL_DERIVATION}
    result = derive_skill_segments(subtasks, schema)
    skills = result["skill_segments"]
    assert len(skills) == 3
    assert skills[0]["skill"] == "approach_skill"
    assert skills[0]["start"] == 0.0
    assert skills[0]["end"] == 2.0
    assert skills[1]["skill"] == "grasp_skill"
    assert skills[1]["start"] == 2.0
    assert skills[1]["end"] == 8.0
    assert skills[2]["skill"] == "place_skill"
    assert skills[2]["start"] == 8.0
    assert skills[2]["end"] == 14.0
    assert result["cycles"][0]["complete"] is True


def test_derive_two_box_episode() -> None:
    subtasks = _one_box_cycle(0.0) + [{"start": 14.0, "end": 16.0, "label": "idle"}] + _one_box_cycle(16.0)
    schema = {"skill_derivation": DEFAULT_SKILL_DERIVATION}
    result = derive_skill_segments(subtasks, schema)
    assert len(result["cycles"]) == 2
    assert len(result["skill_segments"]) == 6


def test_incomplete_cycle_warns() -> None:
    subtasks = [
        {"start": 0.0, "end": 2.0, "label": "reach"},
        {"start": 2.0, "end": 4.0, "label": "pre_grasp"},
    ]
    schema = {"skill_derivation": DEFAULT_SKILL_DERIVATION}
    result = derive_skill_segments(subtasks, schema)
    assert any("incomplete" in w for w in result["warnings"])
