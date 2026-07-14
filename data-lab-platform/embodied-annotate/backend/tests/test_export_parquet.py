"""Tests for training export parquet builders."""

from __future__ import annotations

import json
from pathlib import Path

from export_builders import build_skill_cycles_dataframe, build_skill_segments_dataframe


from dataclasses import dataclass, field


@dataclass
class _Ann:
    skill_segments: list = field(default_factory=list)
    skill_cycles: list = field(default_factory=list)


def test_build_skill_segments_dataframe_includes_subgoal() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "schemas"
        / "box_transport_v1.annotation_schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    annotations = {
        0: _Ann(
            skill_segments=[
                {
                    "start": 0.0,
                    "end": 1.0,
                    "skill": "approach_skill",
                    "subgoal": "move close to the target box and prepare for grasping",
                }
            ]
        )
    }
    df, skill_map = build_skill_segments_dataframe(annotations, schema)
    assert skill_map["approach_skill"] == 0
    assert "subgoal" in df.columns
    assert df.loc["approach_skill", "subgoal"] == (
        "move close to the target box and prepare for grasping"
    )


def test_build_skill_cycles_dataframe_rows() -> None:
    annotations = {
        1: _Ann(
            skill_cycles=[
                {
                    "cycle_id": 0,
                    "start": 0.0,
                    "end": 5.0,
                    "complete": True,
                    "success": True,
                    "fail_reason": "none",
                    "success_source": "auto",
                }
            ]
        )
    }
    df = build_skill_cycles_dataframe(annotations)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["episode_index"] == 1
    assert row["cycle_id"] == 0
    assert row["success"] == True
    assert row["fail_reason"] == "none"
