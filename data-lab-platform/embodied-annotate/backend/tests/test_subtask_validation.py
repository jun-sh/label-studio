"""Tests for backend subtask validation (V-01..V-03)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from annotation_schema import validate_subtasks

BOX_TRANSPORT_LABELS = {
    "subtask_labels": [
        {"id": "idle"},
        {"id": "reach"},
        {"id": "pre_grasp"},
        {"id": "contact"},
        {"id": "lift"},
        {"id": "transport"},
        {"id": "place"},
        {"id": "release"},
    ],
    "validation": {"segments_must_not_overlap": True},
}


def test_validate_subtasks_ok() -> None:
    validate_subtasks(
        BOX_TRANSPORT_LABELS,
        [
            {"start": 0.0, "end": 1.0, "label": "reach"},
            {"start": 1.0, "end": 2.0, "label": "lift"},
        ],
        fps=20.0,
        max_frame=100,
    )


def test_validate_subtasks_unknown_label() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_subtasks(
            BOX_TRANSPORT_LABELS,
            [{"start": 0.0, "end": 1.0, "label": "push"}],
            fps=20.0,
            max_frame=100,
        )
    assert exc.value.status_code == 400
    assert "Unknown subtask label" in exc.value.detail


def test_validate_subtasks_overlap() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_subtasks(
            BOX_TRANSPORT_LABELS,
            [
                {"start": 0.0, "end": 2.0, "label": "reach"},
                {"start": 1.0, "end": 3.0, "label": "lift"},
            ],
            fps=20.0,
            max_frame=100,
        )
    assert exc.value.status_code == 400
    assert "Overlapping" in exc.value.detail


def test_validate_subtasks_out_of_range() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_subtasks(
            BOX_TRANSPORT_LABELS,
            [{"start": 0.0, "end": 10.0, "label": "reach"}],
            fps=20.0,
            max_frame=39,
        )
    assert exc.value.status_code == 400
    assert "outside" in exc.value.detail
