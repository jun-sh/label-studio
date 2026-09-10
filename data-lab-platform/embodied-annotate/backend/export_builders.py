"""Parquet builders for LeRobot training export (export contract v1.0)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from episode_progress import episode_annotation_status
from skill_derivation import skill_subgoal_text

EXPORT_CONTRACT_VERSION = "1.0"

OUTCOME_ENUM = ("success", "fail", "partial")
ANNOTATION_STATUS_ENUM = ("none", "partial", "complete")
DEFAULT_FAIL_REASON_ENUM = (
    "none",
    "slip_grasp",
    "drop_mid_transport",
    "place_offset",
    "incomplete",
    "other",
)

TRAINING_META_ALLOWLIST = (
    "info.json",
    "stats.json",
    "tasks.parquet",
    "episodes",
    "subtasks.parquet",
    "cycles.parquet",
    "episodes_meta.parquet",
    "export_manifest.json",
)

TRAINING_META_BLOCKLIST = (
    "lerobot_annotations.json",
    "skills.parquet",
    "skill_cycles.parquet",
)


def _ann_field(ann: Any, name: str, default: Any = None) -> Any:
    """Read a field from EpisodeAnnotations or a plain dict."""
    if hasattr(ann, name):
        val = getattr(ann, name)
        return default if val is None else val
    if isinstance(ann, dict):
        return ann.get(name, default)
    return default


def _ann_list(ann: Any, name: str) -> list[Any]:
    val = _ann_field(ann, name, [])
    return list(val or [])


def fail_reason_enum(schema: dict[str, Any] | None) -> tuple[str, ...]:
    if not schema:
        return DEFAULT_FAIL_REASON_ENUM
    for spec in schema.get("cycle_fields") or []:
        if spec.get("id") == "fail_reason" and spec.get("values"):
            return tuple(str(v) for v in spec["values"])
    return DEFAULT_FAIL_REASON_ENUM


def build_skill_segments_dataframe(
    annotations: dict[int, Any],
    schema: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Build in-memory skill lookup for frame skill_index assignment (not written to export)."""
    labels = sorted(
        {
            str(seg["skill"])
            for ann in annotations.values()
            for seg in _ann_list(ann, "skill_segments")
            if seg.get("skill")
        }
    )
    rows = []
    for idx, label in enumerate(labels):
        subgoal = skill_subgoal_text(label, schema) or ""
        rows.append({"skill": label, "skill_index": idx, "subgoal": subgoal})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index("skill")
    skill_map = {label: idx for idx, label in enumerate(labels)}
    return df, skill_map


def build_cycles_dataframe(annotations: dict[int, Any]) -> pd.DataFrame:
    """Per-box cycle metadata for meta/cycles.parquet."""
    rows: list[dict[str, Any]] = []
    for episode_index, ann in sorted(annotations.items()):
        cycles = _ann_list(ann, "skill_cycles")
        for cycle in cycles or []:
            rows.append(
                {
                    "episode_index": int(episode_index),
                    "cycle_id": int(cycle.get("cycle_id", 0)),
                    "start": float(cycle.get("start", 0)),
                    "end": float(cycle.get("end", 0)),
                    "complete": bool(cycle.get("complete")),
                    "success": cycle.get("success"),
                    "fail_reason": cycle.get("fail_reason") or "none",
                    "success_source": cycle.get("success_source") or "auto",
                }
            )
    return pd.DataFrame(rows)


def build_skill_cycles_dataframe(annotations: dict[int, Any]) -> pd.DataFrame:
    """Deprecated alias — use build_cycles_dataframe."""
    return build_cycles_dataframe(annotations)


def build_episodes_meta_dataframe(
    annotations: dict[int, Any],
    episode_indices: list[int],
    episodes_df: pd.DataFrame,
    schema: dict[str, Any],
    schema_ref_value: str | None,
) -> pd.DataFrame:
    """Episode-level metadata for meta/episodes_meta.parquet."""
    rows: list[dict[str, Any]] = []
    for ep_idx in episode_indices:
        ann = annotations.get(int(ep_idx))
        task_index: int | None = None
        ep_rows = episodes_df[episodes_df["episode_index"] == ep_idx]
        if not ep_rows.empty and "task_index" in ep_rows.columns:
            raw = ep_rows.iloc[0]["task_index"]
            if pd.notna(raw):
                task_index = int(raw)

        if ann is None:
            rows.append(
                {
                    "episode_index": int(ep_idx),
                    "outcome": None,
                    "box_cycle": None,
                    "task_index": task_index,
                    "annotated": False,
                    "annotation_status": "none",
                    "schema_ref": schema_ref_value,
                }
            )
            continue

        subtasks = _ann_list(ann, "subtasks")
        outcome = _ann_field(ann, "outcome")
        fields = _ann_field(ann, "fields") or {}
        status = episode_annotation_status(subtasks, outcome, fields, schema)
        box_cycle_raw = fields.get("box_cycle")
        box_cycle: int | None
        if box_cycle_raw is None or box_cycle_raw == "":
            box_cycle = None
        else:
            try:
                box_cycle = int(box_cycle_raw)
            except (TypeError, ValueError):
                box_cycle = None

        rows.append(
            {
                "episode_index": int(ep_idx),
                "outcome": outcome,
                "box_cycle": box_cycle,
                "task_index": task_index,
                "annotated": status in ("partial", "complete"),
                "annotation_status": status,
                "schema_ref": schema_ref_value,
            }
        )
    return pd.DataFrame(rows)


def build_subtasks_lookup_dataframe(annotations: dict[int, Any]) -> tuple[pd.DataFrame, dict[str, int]]:
    labels = sorted(
        {
            str(seg["label"])
            for ann in annotations.values()
            for seg in _ann_list(ann, "subtasks")
            if seg.get("label")
        }
    )
    data = [{"subtask": label, "subtask_index": idx} for idx, label in enumerate(labels)]
    df = pd.DataFrame(data)
    if not df.empty:
        df = df.set_index("subtask")
    subtask_map = {label: idx for idx, label in enumerate(labels)}
    return df, subtask_map


def build_export_manifest(
    *,
    schema_ref_value: str | None,
    schema: dict[str, Any] | None,
    row_counts: dict[str, int],
    output_dir: str,
) -> dict[str, Any]:
    return {
        "export_contract_version": EXPORT_CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_ref": schema_ref_value,
        "output_dir": output_dir,
        "files": {
            "frames": "data/**/*.parquet",
            "cycles": "meta/cycles.parquet",
            "episodes_meta": "meta/episodes_meta.parquet",
            "subtasks": "meta/subtasks.parquet",
            "manifest": "meta/export_manifest.json",
        },
        "forbidden_for_training": list(TRAINING_META_BLOCKLIST),
        "foreign_keys": {
            "episode_index": "join frames, cycles.parquet, episodes_meta.parquet",
            "cycle_id": "per-episode box index in cycles.parquet",
        },
        "enums": {
            "outcome": list(OUTCOME_ENUM),
            "fail_reason": list(fail_reason_enum(schema)),
            "annotation_status": list(ANNOTATION_STATUS_ENUM),
        },
        "row_counts": row_counts,
    }
