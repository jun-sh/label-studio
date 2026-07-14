"""Tests for Phase C episode progress and soft validation."""

from __future__ import annotations

from episode_progress import (
    build_annotation_progress,
    episode_annotation_status,
    soft_validate_episode,
)

BOX_TRANSPORT_SCHEMA = {
    "episode_fields": [
        {"id": "box_cycle", "type": "int", "required": True},
        {"id": "notes", "type": "text", "required": False},
    ],
    "cycle_fields": [{"id": "success", "type": "bool"}],
}


def test_status_none() -> None:
    assert episode_annotation_status([], None, {}, BOX_TRANSPORT_SCHEMA) == "none"


def test_status_complete() -> None:
    assert (
        episode_annotation_status(
            [{"start": 0, "end": 1, "label": "reach"}],
            "success",
            {"box_cycle": 1},
            BOX_TRANSPORT_SCHEMA,
        )
        == "complete"
    )


def test_status_partial_missing_outcome() -> None:
    assert (
        episode_annotation_status(
            [{"start": 0, "end": 1, "label": "reach"}],
            None,
            {"box_cycle": 1},
            BOX_TRANSPORT_SCHEMA,
        )
        == "partial"
    )


def test_soft_warn_fail_with_boxes() -> None:
    warnings = soft_validate_episode(
        subtasks=[{"start": 0, "end": 1, "label": "reach"}],
        outcome="fail",
        fields={"box_cycle": 2},
        skill_cycles=[],
        schema=BOX_TRANSPORT_SCHEMA,
    )
    assert any(w["code"] == "W-EP-01" for w in warnings)


def test_soft_warn_success_zero_boxes() -> None:
    warnings = soft_validate_episode(
        subtasks=[],
        outcome="success",
        fields={"box_cycle": 0},
        skill_cycles=[],
        schema=BOX_TRANSPORT_SCHEMA,
    )
    assert any(w["code"] == "W-EP-02" for w in warnings)


def test_soft_warn_box_cycle_mismatch() -> None:
    warnings = soft_validate_episode(
        subtasks=[],
        outcome="partial",
        fields={"box_cycle": 2},
        skill_cycles=[
            {"cycle_id": 0, "success": True},
            {"cycle_id": 1, "success": False},
        ],
        schema=BOX_TRANSPORT_SCHEMA,
    )
    assert any(w["code"] == "W-EP-05" for w in warnings)


def test_build_annotation_progress() -> None:
    class Ann:
        def __init__(self, subtasks, outcome, fields):
            self.subtasks = subtasks
            self.outcome = outcome
            self.fields = fields

    annotations = {
        0: Ann([{"start": 0, "end": 1, "label": "reach"}], "success", {"box_cycle": 1}),
        1: Ann([], None, {}),
    }
    progress = build_annotation_progress(annotations, [0, 1, 2], BOX_TRANSPORT_SCHEMA)
    assert progress["complete"] == 1
    assert progress["none"] == 2
    assert progress["by_episode"]["0"] == "complete"
