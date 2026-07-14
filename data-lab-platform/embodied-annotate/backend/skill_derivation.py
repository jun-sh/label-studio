"""Derive L1 atomic skill segments from L2 subtask phase annotations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

BOX_TRANSPORT_SKILL_LABELS: list[dict[str, Any]] = [
    {
        "id": "approach_skill",
        "order": 0,
        "color": "#10B981",
        "label_zh": "接近技能",
        "label_en": "Approach",
        "hint_zh": "行走趋近至箱体可操作范围",
        "subgoal_en": "move close to the target box and prepare for grasping",
        "subgoal_zh": "趋近目标箱体并准备抓取",
    },
    {
        "id": "grasp_skill",
        "order": 1,
        "color": "#F59E0B",
        "label_zh": "抓取技能",
        "label_en": "Grasp",
        "hint_zh": "预抓 → 接触 → 抬升离地",
        "subgoal_en": "pre-align, contact and lift the box stably",
        "subgoal_zh": "预对位、接触并稳定抬升箱体",
    },
    {
        "id": "place_skill",
        "order": 2,
        "color": "#8B5CF6",
        "label_zh": "放置技能",
        "label_en": "Place",
        "hint_zh": "持箱搬运 → 对位 → 放置 → 松手",
        "subgoal_en": "transport the box and place it on the conveyor belt",
        "subgoal_zh": "搬运箱体并放置到传送带上",
    },
]

DEFAULT_SKILL_DERIVATION: dict[str, Any] = {
    "enabled": True,
    "cycle_anchor_label": "release",
    "cycle_start_labels": ["reach"],
    "skills": {
        "approach_skill": {
            "from_label": "reach",
            "until_label": "pre_grasp",
            "until_exclusive": True,
        },
        "grasp_skill": {
            "from_label": "pre_grasp",
            "until_label": "lift",
            "until_inclusive": True,
        },
        "place_skill": {
            "from_label": "transport",
            "until_label": "release",
            "until_inclusive": True,
        },
    },
}


def skill_derivation_config(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not schema:
        return None
    cfg = schema.get("skill_derivation")
    if not cfg or not cfg.get("enabled"):
        return None
    merged = deepcopy(DEFAULT_SKILL_DERIVATION)
    merged.update({k: v for k, v in cfg.items() if k != "skills"})
    if isinstance(cfg.get("skills"), dict):
        for skill_id, rule in cfg["skills"].items():
            merged.setdefault("skills", {})
            merged["skills"][skill_id] = {
                **merged["skills"].get(skill_id, {}),
                **rule,
            }
    return merged


def _sorted_segments(subtasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [s for s in subtasks if s.get("label") is not None],
        key=lambda s: (float(s.get("start", 0)), float(s.get("end", 0))),
    )


def _segments_by_label(segments: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for seg in segments:
        label = str(seg.get("label") or "")
        out.setdefault(label, []).append(seg)
    return out


def _first_start(segments: list[dict[str, Any]], label: str) -> float | None:
    for seg in _segments_by_label(segments).get(label, []):
        return float(seg["start"])
    return None


def _last_end(segments: list[dict[str, Any]], label: str) -> float | None:
    ends = [float(seg["end"]) for seg in _segments_by_label(segments).get(label, [])]
    return max(ends) if ends else None


CYCLE_USER_FIELDS = ("success", "fail_reason", "success_source", "cycle_notes")
CYCLE_STRUCTURE_EPSILON_SEC = 0.05


def _cycle_time_bounds(cycle_segments: list[dict[str, Any]]) -> tuple[float, float]:
    if not cycle_segments:
        return 0.0, 0.0
    starts = [float(seg["start"]) for seg in cycle_segments]
    ends = [float(seg["end"]) for seg in cycle_segments]
    return min(starts), max(ends)


def _cycle_structure_similar(old: dict[str, Any], new: dict[str, Any]) -> bool:
    try:
        old_start = float(old.get("start", 0))
        old_end = float(old.get("end", 0))
        new_start = float(new.get("start", 0))
        new_end = float(new.get("end", 0))
    except (TypeError, ValueError):
        return False
    return (
        abs(old_start - new_start) <= CYCLE_STRUCTURE_EPSILON_SEC
        and abs(old_end - new_end) <= CYCLE_STRUCTURE_EPSILON_SEC
    )


def auto_cycle_defaults(cycle: dict[str, Any]) -> dict[str, Any]:
    """Production baseline: infer per-box success from L2 phase completeness."""
    out = dict(cycle)
    complete = bool(cycle.get("complete"))
    out["success"] = complete
    out["fail_reason"] = "none" if complete else "incomplete"
    out["success_source"] = "auto"
    return out


def skill_subgoal_text(skill_id: str, schema: dict[str, Any] | None) -> str | None:
    labels = (schema or {}).get("skill_labels") or BOX_TRANSPORT_SKILL_LABELS
    for lbl in labels:
        if str(lbl.get("id")) == skill_id:
            return lbl.get("subgoal_en") or lbl.get("subgoal_zh")
    return None


def merge_skill_cycles(
    previous: list[dict[str, Any]] | None,
    derived: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve manual per-cycle overrides when structure is unchanged."""
    prev_by_id = {
        int(cycle["cycle_id"]): cycle
        for cycle in (previous or [])
        if cycle.get("cycle_id") is not None
    }
    merged: list[dict[str, Any]] = []
    for cycle in derived:
        cid = int(cycle["cycle_id"])
        old = prev_by_id.get(cid)
        out = dict(cycle)
        if old and _cycle_structure_similar(old, cycle) and (
            old.get("success_source") == "manual"
            or (old.get("success_source") is None and old.get("success") is not None)
        ):
            for key in CYCLE_USER_FIELDS:
                if key in old:
                    out[key] = old[key]
        else:
            out = auto_cycle_defaults(out)
        merged.append(out)
    return merged


def count_successful_cycles(cycles: list[dict[str, Any]] | None) -> int | None:
    """Return success count from auto or manual per-cycle success flags."""
    if not cycles:
        return None
    if not any(isinstance(cycle.get("success"), bool) for cycle in cycles):
        return None
    return sum(1 for cycle in cycles if cycle.get("success") is True)


def sync_box_cycle_from_cycles(
    cycles: list[dict[str, Any]] | None,
    fields: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive episode box_cycle summary from per-cycle success flags."""
    fields = dict(fields or {})
    counted = count_successful_cycles(cycles)
    if counted is not None:
        fields["box_cycle"] = counted
    return fields


def split_subtasks_into_cycles(
    subtasks: list[dict[str, Any]],
    *,
    cycle_start_labels: list[str] | None = None,
) -> list[list[dict[str, Any]]]:
    """Group subtasks into box cycles (each anchored by reach … release pattern)."""
    segments = _sorted_segments(subtasks)
    if not segments:
        return []

    starts = set(cycle_start_labels or ["reach"])
    cycles: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for seg in segments:
        label = str(seg.get("label") or "")
        if label in starts and current:
            has_release = any(s.get("label") == "release" for s in current)
            if has_release:
                cycles.append(current)
                current = [seg]
                continue
        current.append(seg)

    if current:
        cycles.append(current)

    return cycles


def _derive_skill_segment(
    cycle_segments: list[dict[str, Any]],
    skill_id: str,
    rule: dict[str, Any],
) -> dict[str, Any] | None:
    from_label = str(rule.get("from_label") or "")
    until_label = str(rule.get("until_label") or "")
    until_exclusive = bool(rule.get("until_exclusive"))
    until_inclusive = bool(rule.get("until_inclusive", True))

    start = _first_start(cycle_segments, from_label)
    if start is None:
        return None

    until_start = _first_start(cycle_segments, until_label)
    until_end = _last_end(cycle_segments, until_label)

    if until_exclusive:
        if until_start is None:
            return None
        end = until_start
    elif until_inclusive:
        if until_end is None:
            return None
        end = until_end
    else:
        if until_start is None:
            return None
        end = until_start

    if end <= start:
        return None

    return {
        "start": start,
        "end": end,
        "skill": skill_id,
        "source": "auto",
    }


def derive_skill_segments(
    subtasks: list[dict[str, Any]],
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive skill_segments and per-cycle metadata from subtask phases."""
    cfg = skill_derivation_config(schema) or skill_derivation_config(
        {"skill_derivation": DEFAULT_SKILL_DERIVATION}
    )
    if not cfg:
        return {"skill_segments": [], "cycles": [], "warnings": []}

    cycles_raw = split_subtasks_into_cycles(
        subtasks,
        cycle_start_labels=list(cfg.get("cycle_start_labels") or ["reach"]),
    )

    skill_segments: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    warnings: list[str] = []
    skill_rules: dict[str, Any] = cfg.get("skills") or {}

    for cycle_id, cycle_segs in enumerate(cycles_raw):
        cycle_skills: list[dict[str, Any]] = []
        labels_present = {str(s.get("label")) for s in cycle_segs}

        for skill_id, rule in skill_rules.items():
            seg = _derive_skill_segment(cycle_segs, skill_id, rule)
            if seg is None:
                warnings.append(f"cycle_{cycle_id}: missing segment for {skill_id}")
                continue
            subgoal = skill_subgoal_text(skill_id, schema)
            seg = {**seg, "cycle_id": cycle_id}
            if subgoal:
                seg["subgoal"] = subgoal
            cycle_skills.append(seg)
            skill_segments.append(seg)

        complete = all(
            lbl in labels_present
            for lbl in ("reach", "pre_grasp", "lift", "transport", "release")
        )
        start_t, end_t = _cycle_time_bounds(cycle_segs)
        cycles.append(
            {
                "cycle_id": cycle_id,
                "start": start_t,
                "end": end_t,
                "complete": complete,
                "labels": sorted(labels_present),
                "skill_count": len(cycle_skills),
            }
        )
        cycles[-1] = auto_cycle_defaults(cycles[-1])

        if not complete:
            warnings.append(f"cycle_{cycle_id}: incomplete phase pattern")

    skill_segments.sort(key=lambda s: (float(s["start"]), float(s["end"])))
    return {
        "skill_segments": skill_segments,
        "cycles": cycles,
        "warnings": warnings,
    }


def apply_skill_derivation(
    subtasks: list[dict[str, Any]],
    schema: dict[str, Any] | None,
    previous_cycles: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Return (skill_segments, cycles, warnings). Empty when derivation disabled."""
    result = derive_skill_segments(subtasks, schema)
    cycles = merge_skill_cycles(previous_cycles, result["cycles"])
    return (
        result["skill_segments"],
        cycles,
        result["warnings"],
    )
