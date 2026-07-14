"""Resolve per-dataset annotation schemas (subtask labels, episode fields, validation)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "collection.manifest.json"
DATASET_SCHEMA_FILENAME = "annotation.schema.json"

DEFAULT_MANIPULATION_SCHEMA: dict[str, Any] = {
    "schema_id": "default_manipulation_v1",
    "schema_version": 1,
    "subtask_labels": [
        {"id": "idle", "order": 0, "color": "#9CA3AF", "label_zh": "空闲", "hint_zh": "空闲/等待，手未参与任务"},
        {"id": "reach", "order": 1, "color": "#34D399", "label_zh": "接近", "hint_zh": "接近目标，尚未接触"},
        {"id": "pre_grasp", "order": 2, "color": "#6EE7B7", "label_zh": "预抓取", "hint_zh": "对准、张开，准备抓取"},
        {"id": "contact", "order": 3, "color": "#FB923C", "label_zh": "接触", "hint_zh": "刚触碰物体至抓稳前"},
        {"id": "lift", "order": 4, "color": "#60A5FA", "label_zh": "抬起", "hint_zh": "物体离开支撑面"},
        {"id": "transport", "order": 5, "color": "#3B82F6", "label_zh": "搬运", "hint_zh": "拿着物体水平移动"},
        {"id": "place", "order": 6, "color": "#A78BFA", "label_zh": "放置", "hint_zh": "朝放置位下降"},
        {"id": "release", "order": 7, "color": "#F472B6", "label_zh": "释放", "hint_zh": "松手，物体脱离"},
    ],
    "episode_fields": [],
    "validation": {"segments_must_not_overlap": True, "gap_warn_frames": 10},
}


def schema_ref(schema: dict[str, Any]) -> str:
    return f"{schema.get('schema_id', 'unknown')}@{schema.get('schema_version', 1)}"


def _deep_merge_schema(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    if not override:
        return deepcopy(base)
    merged = deepcopy(base)
    for key, value in override.items():
        if key in ("subtask_labels", "episode_fields", "timeline", "skill_labels", "cycle_fields") and value:
            merged[key] = deepcopy(value)
        elif key == "skill_derivation" and isinstance(value, dict):
            merged["skill_derivation"] = deepcopy(value)
        elif key == "validation" and isinstance(value, dict):
            merged["validation"] = {**(merged.get("validation") or {}), **value}
        else:
            merged[key] = value
    return merged


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def resolve_annotation_schema(dataset_root: Path) -> dict[str, Any]:
    """Resolve schema for a loaded LeRobot dataset root."""
    root = dataset_root.resolve()
    schema = deepcopy(DEFAULT_MANIPULATION_SCHEMA)

    dataset_schema = _load_json_file(root / DATASET_SCHEMA_FILENAME)
    if dataset_schema and dataset_schema.get("subtask_labels"):
        schema = _deep_merge_schema(schema, dataset_schema)

    parent = root.parent
    manifest = _load_json_file(parent / MANIFEST_FILENAME)
    if manifest:
        collection_schema = manifest.get("annotation_schema")
        package_schema = None
        for raw_pkg in manifest.get("packages") or []:
            if isinstance(raw_pkg, dict) and str(raw_pkg.get("id")) == root.name:
                package_schema = raw_pkg.get("annotation_schema")
                break
        if collection_schema:
            schema = _deep_merge_schema(schema, collection_schema)
        if package_schema:
            schema = _deep_merge_schema(schema, package_schema)

    schema.setdefault("validation", {})
    schema["validation"].setdefault("gap_warn_frames", 10)
    schema["validation"].setdefault("segments_must_not_overlap", True)
    schema.setdefault("episode_fields", [])
    schema.setdefault("cycle_fields", [])
    return schema


def cycle_field_specs(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return list(schema.get("cycle_fields") or [])


def validate_skill_cycles(schema: dict[str, Any], cycles: list[dict[str, Any]] | None) -> None:
    from fastapi import HTTPException

    if not cycles:
        return
    specs = {str(spec["id"]): spec for spec in cycle_field_specs(schema) if spec.get("id")}
    if not specs:
        return

    for cycle in cycles:
        cid = cycle.get("cycle_id")
        for field_id, spec in specs.items():
            if field_id not in cycle:
                continue
            value = cycle.get(field_id)
            if value is None or value == "":
                continue
            field_type = spec.get("type", "text")
            if field_type == "bool" and not isinstance(value, bool):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cycle {cid} field '{field_id}' must be a boolean",
                )
            if field_type == "enum":
                values = spec.get("values") or []
                if str(value) not in values:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cycle {cid} field '{field_id}' must be one of {values}",
                    )
            if field_type == "text" and spec.get("max_length"):
                if len(str(value)) > int(spec["max_length"]):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cycle {cid} field '{field_id}' exceeds max length",
                    )


def allowed_label_ids(schema: dict[str, Any]) -> set[str]:
    return {str(lbl["id"]) for lbl in schema.get("subtask_labels") or [] if lbl.get("id")}


def allowed_skill_ids(schema: dict[str, Any]) -> set[str]:
    return {str(lbl["id"]) for lbl in schema.get("skill_labels") or [] if lbl.get("id")}


def _seg_to_frames(seg: dict[str, Any], fps: float) -> tuple[int, int]:
    start = max(0, round(float(seg.get("start", 0)) * fps))
    end = max(0, int(float(seg.get("end", 0)) * fps + 0.999999) - 1)
    return start, end


def validate_subtasks(
    schema: dict[str, Any],
    subtasks: list[dict[str, Any]],
    *,
    fps: float = 30.0,
    max_frame: int | None = None,
) -> None:
    """Hard validation aligned with frontend V-01..V-03."""
    from fastapi import HTTPException

    allowed = allowed_label_ids(schema)
    must_not_overlap = bool((schema.get("validation") or {}).get("segments_must_not_overlap", True))
    frames_list: list[tuple[int, str, int, int]] = []

    for index, seg in enumerate(subtasks):
        label = str(seg.get("label") or "")
        if allowed and label and label not in allowed:
            raise HTTPException(status_code=400, detail=f"Unknown subtask label '{label}'")
        start_f, end_f = _seg_to_frames(seg, fps)
        if start_f > end_f:
            raise HTTPException(
                status_code=400,
                detail=f"Subtask '{label}' start frame {start_f} is after end frame {end_f}",
            )
        if max_frame is not None and (start_f < 0 or end_f > max_frame):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Subtask '{label}' frame range [{start_f}, {end_f}] "
                    f"is outside [0, {max_frame}]"
                ),
            )
        frames_list.append((index, label, start_f, end_f))

    if not must_not_overlap:
        return

    for i in range(len(frames_list)):
        for j in range(i + 1, len(frames_list)):
            _, label_a, start_a, end_a = frames_list[i]
            _, label_b, start_b, end_b = frames_list[j]
            if start_a <= end_b and start_b <= end_a:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Overlapping subtasks: {label_a} f{start_a}-f{end_a} "
                        f"and {label_b} f{start_b}-f{end_b}"
                    ),
                )


def skill_derivation_enabled(schema: dict[str, Any]) -> bool:
    cfg = schema.get("skill_derivation")
    return bool(cfg and cfg.get("enabled"))


def filter_episode_fields(schema: dict[str, Any], fields: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only fields declared in schema (drops deprecated keys like legacy notes)."""
    allowed = {str(spec["id"]) for spec in schema.get("episode_fields") or [] if spec.get("id")}
    return {key: value for key, value in (fields or {}).items() if key in allowed}


def validate_episode_fields(schema: dict[str, Any], fields: dict[str, Any] | None) -> None:
    from fastapi import HTTPException

    fields = fields or {}
    specs = schema.get("episode_fields") or []
    allowed_ids = {str(spec["id"]) for spec in specs if spec.get("id")}

    for key in fields:
        if key not in allowed_ids:
            raise HTTPException(status_code=400, detail=f"Unknown episode field '{key}'")

    for spec in specs:
        field_id = str(spec.get("id") or "")
        if not field_id:
            continue
        required = bool(spec.get("required"))
        value = fields.get(field_id)
        if required and (value is None or value == ""):
            raise HTTPException(status_code=400, detail=f"Episode field '{field_id}' is required")

        if value is None or value == "":
            continue

        field_type = spec.get("type", "text")
        if field_type == "int":
            try:
                iv = int(value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"Field '{field_id}' must be an integer") from exc
            if "min" in spec and iv < spec["min"]:
                raise HTTPException(status_code=400, detail=f"Field '{field_id}' must be >= {spec['min']}")
            if "max" in spec and iv > spec["max"]:
                raise HTTPException(status_code=400, detail=f"Field '{field_id}' must be <= {spec['max']}")
        elif field_type == "float":
            try:
                float(value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"Field '{field_id}' must be a number") from exc
        elif field_type == "enum":
            values = spec.get("values") or []
            if str(value) not in values:
                raise HTTPException(
                    status_code=400,
                    detail=f"Field '{field_id}' must be one of {values}",
                )
        elif field_type == "bool":
            if not isinstance(value, bool):
                raise HTTPException(status_code=400, detail=f"Field '{field_id}' must be a boolean")
