"""Tests for EmbodiedDataConfig (E2)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from training.embodied_data_config import EmbodiedDataConfig


def _write_minimal_export(root: Path, *, with_json: bool = False) -> None:
    meta = root / "meta"
    data = root / "data" / "chunk-000"
    meta.mkdir(parents=True)
    data.mkdir(parents=True)

    pd.DataFrame(
        {
            "episode_index": [0, 0, 1, 1],
            "frame_index": [0, 1, 0, 1],
            "subtask_index": [-1, -1, -1, -1],
        }
    ).to_parquet(data / "file-000.parquet", index=False)

    pd.DataFrame(
        {
            "episode_index": [0],
            "cycle_id": [0],
            "start": [0.0],
            "end": [1.0],
            "complete": [True],
            "success": [False],
            "fail_reason": ["slip_grasp"],
            "success_source": ["manual"],
        }
    ).to_parquet(meta / "cycles.parquet", index=False)

    pd.DataFrame(
        {
            "episode_index": [0, 1],
            "outcome": ["fail", None],
            "box_cycle": [0, None],
            "task_index": [0, 0],
            "annotated": [True, False],
            "annotation_status": ["complete", "none"],
            "schema_ref": ["box_transport_v1@1", "box_transport_v1@1"],
        }
    ).to_parquet(meta / "episodes_meta.parquet", index=False)

    (meta / "export_manifest.json").write_text(
        json.dumps(
            {
                "export_contract_version": "1.0",
                "forbidden_for_training": ["lerobot_annotations.json", "skills.parquet"],
            }
        ),
        encoding="utf-8",
    )

    if with_json:
        (meta / "lerobot_annotations.json").write_text("{}", encoding="utf-8")


def test_rejects_training_bundle_with_annotation_json(tmp_path: Path) -> None:
    _write_minimal_export(tmp_path, with_json=True)
    with pytest.raises(ValueError, match="lerobot_annotations.json"):
        EmbodiedDataConfig(tmp_path)


def test_filter_frames_by_fail_reason(tmp_path: Path) -> None:
    _write_minimal_export(tmp_path)
    cfg = EmbodiedDataConfig(tmp_path)
    assert cfg.episode_indices_for_fail_reason("slip_grasp") == [0]
    frames = cfg.load_frames_for_fail_reason("slip_grasp")
    assert set(frames["episode_index"].tolist()) == {0}
