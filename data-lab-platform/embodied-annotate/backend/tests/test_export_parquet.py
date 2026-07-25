"""Tests for training export parquet builders (export contract v1.0)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from export_builders import (
    TRAINING_META_BLOCKLIST,
    build_cycles_dataframe,
    build_episodes_meta_dataframe,
    build_export_manifest,
    build_skill_segments_dataframe,
)
from export_staging import stage_training_meta


@dataclass
class _Ann:
    subtasks: list = field(default_factory=list)
    skill_segments: list = field(default_factory=list)
    skill_cycles: list = field(default_factory=list)
    outcome: str | None = None
    fields: dict = field(default_factory=dict)


def _schema() -> dict:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "schemas"
        / "box_transport_v1.annotation_schema.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


def test_build_skill_segments_dataframe_in_memory_only() -> None:
    schema = _schema()
    annotations = {
        0: _Ann(
            skill_segments=[
                {"start": 0.0, "end": 1.0, "skill": "approach_skill"},
            ]
        )
    }
    df, skill_map = build_skill_segments_dataframe(annotations, schema)
    assert skill_map["approach_skill"] == 0
    assert "subgoal" in df.columns


def test_build_cycles_dataframe_rows() -> None:
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
    df = build_cycles_dataframe(annotations)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["episode_index"] == 1
    assert row["fail_reason"] == "none"


def test_build_episodes_meta_dataframe() -> None:
    schema = _schema()
    annotations = {
        0: _Ann(
            subtasks=[{"start": 0.0, "end": 1.0, "label": "idle"}],
            outcome="success",
            fields={"box_cycle": 1},
        )
    }
    episodes_df = pd.DataFrame({"episode_index": [0], "task_index": [0]})
    df = build_episodes_meta_dataframe(annotations, [0], episodes_df, schema, "box_transport_v1@1")
    assert len(df) == 1
    assert df.iloc[0]["outcome"] == "success"
    assert df.iloc[0]["box_cycle"] == 1
    assert df.iloc[0]["annotated"] == True  # noqa: E712


def test_stage_training_meta_excludes_json(tmp_path: Path) -> None:
    src = tmp_path / "src_meta"
    dst = tmp_path / "dst_meta"
    src.mkdir()
    (src / "info.json").write_text("{}", encoding="utf-8")
    (src / "lerobot_annotations.json").write_text("{}", encoding="utf-8")
    (src / "skills.parquet").write_bytes(b"")
    (src / "skill_cycles.parquet").write_bytes(b"")
    ep_dir = src / "episodes" / "chunk-000"
    ep_dir.mkdir(parents=True)
    (ep_dir / "file-000.parquet").write_bytes(b"")

    stage_training_meta(src, dst)

    assert (dst / "info.json").is_file()
    assert (dst / "episodes" / "chunk-000" / "file-000.parquet").is_file()
    assert not (dst / "lerobot_annotations.json").exists()
    assert not (dst / "skills.parquet").exists()
    assert not (dst / "skill_cycles.parquet").exists()


def test_export_manifest_blocklist() -> None:
    manifest = build_export_manifest(
        schema_ref_value="box_transport_v1@1",
        schema=_schema(),
        row_counts={"cycles": 1},
        output_dir="/tmp/out",
    )
    assert manifest["export_contract_version"] == "1.0"
    assert "lerobot_annotations.json" in manifest["forbidden_for_training"]
    assert "skills.parquet" in manifest["forbidden_for_training"]
    assert "slip_grasp" in manifest["enums"]["fail_reason"]


def test_training_meta_blocklist_names() -> None:
    assert "lerobot_annotations.json" in TRAINING_META_BLOCKLIST
    assert "skills.parquet" in TRAINING_META_BLOCKLIST
    assert "skill_cycles.parquet" in TRAINING_META_BLOCKLIST
