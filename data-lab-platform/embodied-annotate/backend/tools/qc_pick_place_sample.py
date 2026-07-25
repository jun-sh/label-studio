#!/usr/bin/env python3
"""QC analysis for pick_place annotations (pilot + 10% sample plan)."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402

from annotation_jobs_builder import extract_episode_task_text  # noqa: E402
from annotation_schema import resolve_annotation_schema, validate_subtasks  # noqa: E402
from episode_progress import episode_annotation_status  # noqa: E402
from skill_derivation import derive_skill_segments  # noqa: E402

PHASE_ORDER = [
    "idle",
    "reach",
    "pre_grasp",
    "contact",
    "lift",
    "transport",
    "place",
    "release",
    "idle",
]
ORDER_IDX = {p: i for i, p in enumerate(PHASE_ORDER)}


def load_episodes_df(package_root: Path) -> pd.DataFrame:
    ep_dir = package_root / "meta" / "episodes"
    files = sorted(ep_dir.rglob("*.parquet"))
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def episode_task_text(episodes_df: pd.DataFrame, episode_index: int) -> str | None:
    rows = episodes_df[episodes_df["episode_index"] == episode_index]
    if rows.empty:
        return None
    return extract_episode_task_text(rows.iloc[0])


def check_phase_sequence(subtasks: list[dict[str, Any]]) -> list[str]:
    """Flag backward transitions within a manipulation round (ignoring idle gaps)."""
    issues: list[str] = []
    sorted_segs = sorted(subtasks, key=lambda s: (float(s["start"]), float(s["end"])))
    core = [s for s in sorted_segs if s.get("label") != "idle"]
    for i in range(1, len(core)):
        prev = str(core[i - 1].get("label"))
        curr = str(core[i].get("label"))
        if ORDER_IDX.get(curr, 99) < ORDER_IDX.get(prev, 99):
            issues.append(
                f"phase_regression: {prev}@{core[i-1]['end']}s → {curr}@{core[i]['start']}s"
            )
    return issues


def check_gaps(subtasks: list[dict[str, Any]], fps: float, threshold_frames: int = 10) -> list[str]:
    issues: list[str] = []
    sorted_segs = sorted(subtasks, key=lambda s: float(s["start"]))
    for i in range(1, len(sorted_segs)):
        gap = float(sorted_segs[i]["start"]) - float(sorted_segs[i - 1]["end"])
        if gap > threshold_frames / fps + 1e-6:
            issues.append(
                f"gap_{gap:.2f}s between {sorted_segs[i-1].get('label')} and {sorted_segs[i].get('label')}"
            )
    return issues


def reach_pre_grasp_ratio(subtasks: list[dict[str, Any]]) -> dict[str, float]:
    reach_d = pre_d = 0.0
    for seg in subtasks:
        dur = float(seg["end"]) - float(seg["start"])
        if seg.get("label") == "reach":
            reach_d += dur
        elif seg.get("label") == "pre_grasp":
            pre_d += dur
    total = reach_d + pre_d
    return {
        "reach_sec": reach_d,
        "pre_grasp_sec": pre_d,
        "pre_grasp_ratio": (pre_d / total) if total > 0 else 0.0,
    }


def qc_episode(
    *,
    episode_index: int,
    ann: dict[str, Any],
    schema: dict[str, Any],
    fps: float,
    duration: float,
    task_text: str | None,
) -> dict[str, Any]:
    subtasks = ann.get("subtasks") or []
    fields = ann.get("fields") or {}
    issues: list[str] = []
    warnings: list[str] = []

    try:
        validate_subtasks(schema, subtasks, fps=fps, max_frame=int(duration * fps))
    except Exception as exc:
        issues.append(f"validation_error: {exc}")

    issues.extend(check_phase_sequence(subtasks))
    warnings.extend(check_gaps(subtasks, fps))

    if ann.get("outcome") not in {"success", "fail", "partial"}:
        issues.append("missing_outcome")

    if fields.get("box_cycle") is not None:
        warnings.append("unexpected_field: box_cycle (UniFranka should use outcome only)")

    if ann.get("skill_cycles"):
        warnings.append("skill_cycles_present (UniFranka L1.5 disabled; harmless but non-canonical)")

    status = episode_annotation_status(subtasks, ann.get("outcome"), fields, schema)
    if status != "complete":
        issues.append(f"status_{status}")

    trailing_idle = False
    if subtasks:
        last = sorted(subtasks, key=lambda s: float(s["end"]))[-1]
        trailing_idle = last.get("label") == "idle"

    derived = derive_skill_segments(subtasks, schema)
    ratios = reach_pre_grasp_ratio(subtasks)

    return {
        "episode_index": episode_index,
        "task_text": task_text,
        "duration_sec": duration,
        "status": status,
        "outcome": ann.get("outcome"),
        "segment_count": len(subtasks),
        "labels_used": sorted({s.get("label") for s in subtasks}),
        "reach_pre_grasp": ratios,
        "trailing_idle": trailing_idle,
        "issues": issues,
        "warnings": warnings,
        "subtasks": subtasks,
        "skill_segments": derived.get("skill_segments") or ann.get("skill_segments"),
    }


def sample_episode_indices(
    all_indices: list[int],
    *,
    annotated_indices: list[int],
    sample_pct: float,
    seed: int,
    by_task: dict[int, str | None],
) -> list[int]:
    """10% of package total, prioritizing annotated for pilot; stratify by task_text when possible."""
    if not annotated_indices:
        return []
    n_sample = max(1, math.ceil(len(all_indices) * sample_pct))
    if len(annotated_indices) <= n_sample:
        return sorted(annotated_indices)

    rng = random.Random(seed)
    by_text: dict[str, list[int]] = {}
    for idx in annotated_indices:
        text = by_task.get(idx) or "unknown"
        by_text.setdefault(text, []).append(idx)

    picked: list[int] = []
    texts = sorted(by_text)
    per = max(1, n_sample // len(texts))
    for text in texts:
        pool = list(by_text[text])
        rng.shuffle(pool)
        picked.extend(pool[:per])
    if len(picked) < n_sample:
        rest = [i for i in annotated_indices if i not in picked]
        rng.shuffle(rest)
        picked.extend(rest[: n_sample - len(picked)])
    return sorted(picked[:n_sample])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", default="681496")
    parser.add_argument("--collection-dir", type=Path, required=True)
    parser.add_argument("--sample-pct", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=20260720)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    package_root = args.collection_dir / args.package
    schema = resolve_annotation_schema(package_root)
    fps = float(json.loads((package_root / "meta/info.json").read_text()).get("fps", 5))
    episodes_df = load_episodes_df(package_root)
    ann_path = package_root / "meta/lerobot_annotations.json"
    ann_data = json.loads(ann_path.read_text(encoding="utf-8"))
    annotations = {int(k): v for k, v in (ann_data.get("episodes") or {}).items()}

    all_indices = sorted(int(x) for x in episodes_df["episode_index"].tolist())
    annotated_indices = sorted(annotations.keys())
    by_task = {idx: episode_task_text(episodes_df, idx) for idx in all_indices}

    sample_set = sample_episode_indices(
        all_indices,
        annotated_indices=annotated_indices,
        sample_pct=args.sample_pct,
        seed=args.seed,
        by_task=by_task,
    )

    results = []
    for ep_idx in sample_set:
        row = episodes_df[episodes_df["episode_index"] == ep_idx].iloc[0]
        length = int(row.get("length", 0))
        duration = length / fps if fps else 0.0
        ann = annotations.get(ep_idx, {})
        results.append(
            qc_episode(
                episode_index=ep_idx,
                ann=ann,
                schema=schema,
                fps=fps,
                duration=duration,
                task_text=by_task.get(ep_idx),
            )
        )

    report = {
        "package_id": args.package,
        "schema_ref": ann_data.get("schema_ref"),
        "total_episodes": len(all_indices),
        "annotated_count": len(annotated_indices),
        "annotated_indices": annotated_indices,
        "sample_pct_target": args.sample_pct,
        "sample_indices": sample_set,
        "sample_reviewed": len(results),
        "pass_count": sum(1 for r in results if not r["issues"]),
        "episodes": results,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if report["pass_count"] == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
