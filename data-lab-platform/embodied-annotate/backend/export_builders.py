"""Parquet builders for LeRobot training export."""

from __future__ import annotations

from typing import Any

import pandas as pd

from skill_derivation import skill_subgoal_text


def build_skill_segments_dataframe(
    annotations: dict[int, Any],
    schema: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    labels = sorted(
        {
            str(seg["skill"])
            for ann in annotations.values()
            for seg in getattr(ann, "skill_segments", None) or ann.get("skill_segments", [])
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


def build_skill_cycles_dataframe(annotations: dict[int, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for episode_index, ann in sorted(annotations.items()):
        cycles = getattr(ann, "skill_cycles", None) or ann.get("skill_cycles", [])
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
