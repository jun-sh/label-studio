"""Episode annotation progress and soft consistency checks (Phase C)."""

from __future__ import annotations

from typing import Any

VALID_OUTCOMES = frozenset({"success", "fail", "partial"})


def _has_box_cycle_field(schema: dict[str, Any]) -> bool:
    return any(spec.get("id") == "box_cycle" for spec in schema.get("episode_fields") or [])


def _box_cycle_value(fields: dict[str, Any] | None) -> int | None:
    if not fields or "box_cycle" not in fields:
        return None
    try:
        return int(fields["box_cycle"])
    except (TypeError, ValueError):
        return None


def _required_fields_missing(fields: dict[str, Any] | None, schema: dict[str, Any]) -> bool:
    for spec in schema.get("episode_fields") or []:
        if not spec.get("required"):
            continue
        field_id = str(spec.get("id") or "")
        if not field_id:
            continue
        value = (fields or {}).get(field_id)
        if value is None or value == "":
            return True
    return False


def episode_annotation_status(
    subtasks: list[dict[str, Any]] | None,
    outcome: str | None,
    fields: dict[str, Any] | None,
    schema: dict[str, Any],
) -> str:
    """Return none | partial | complete for sidebar progress."""
    has_subtasks = bool(subtasks)
    has_outcome = outcome in VALID_OUTCOMES
    has_fields = bool(fields)

    if not has_subtasks and not has_outcome and not has_fields:
        return "none"

    if has_subtasks and has_outcome and not _required_fields_missing(fields, schema):
        return "complete"
    return "partial"


def soft_validate_episode(
    *,
    subtasks: list[dict[str, Any]] | None,
    outcome: str | None,
    fields: dict[str, Any] | None,
    skill_cycles: list[dict[str, Any]] | None,
    schema: dict[str, Any],
) -> list[dict[str, str]]:
    """Non-blocking warnings (e.g. box_cycle vs outcome)."""
    warnings: list[dict[str, str]] = []
    fields = fields or {}
    cycles = skill_cycles or []

    if outcome not in VALID_OUTCOMES and (subtasks or fields):
        warnings.append({
            "code": "W-EP-06",
            "message": "Outcome is not selected",
        })

    if not _has_box_cycle_field(schema):
        return warnings

    box_cycle = _box_cycle_value(fields)
    if box_cycle is None:
        return warnings

    if outcome == "fail" and box_cycle > 0:
        warnings.append({
            "code": "W-EP-01",
            "message": f"Outcome is fail but box_cycle is {box_cycle}",
        })
    if outcome == "success" and box_cycle == 0:
        warnings.append({
            "code": "W-EP-02",
            "message": "Outcome is success but box_cycle is 0",
        })

    explicit_success = [c for c in cycles if c.get("success") is not None]
    if explicit_success:
        success_count = sum(1 for c in cycles if c.get("success") is True)
        if box_cycle != success_count:
            warnings.append({
                "code": "W-EP-05",
                "message": f"box_cycle ({box_cycle}) differs from per-box success count ({success_count})",
            })

        if outcome == "fail" and any(c.get("success") is True for c in cycles):
            warnings.append({
                "code": "W-EP-03",
                "message": "Outcome is fail but at least one box is marked successful",
            })
        if outcome == "success" and any(c.get("success") is False for c in cycles):
            warnings.append({
                "code": "W-EP-04",
                "message": "Outcome is success but at least one box is marked failed",
            })

    if outcome in VALID_OUTCOMES and cycles:
        unset = [c for c in cycles if c.get("success") is None]
        if unset:
            warnings.append({
                "code": "W-EP-07",
                "message": f"{len(unset)} box cycle(s) have unset success",
            })

    return warnings


def build_annotation_progress(
    annotations: dict[int, Any],
    episode_indices: list[int],
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate counts and per-episode status map."""
    by_episode: dict[str, str] = {}
    counts = {"complete": 0, "partial": 0, "none": 0}

    for ep_idx in episode_indices:
        ann = annotations.get(ep_idx)
        if ann is None:
            status = "none"
        else:
            status = episode_annotation_status(
                getattr(ann, "subtasks", None) or (ann.get("subtasks") if isinstance(ann, dict) else None),
                getattr(ann, "outcome", None) if not isinstance(ann, dict) else ann.get("outcome"),
                getattr(ann, "fields", None) if not isinstance(ann, dict) else ann.get("fields"),
                schema,
            )
        by_episode[str(ep_idx)] = status
        counts[status] += 1

    total = len(episode_indices)
    annotated = counts["complete"] + counts["partial"]
    return {
        "total": total,
        "complete": counts["complete"],
        "partial": counts["partial"],
        "none": counts["none"],
        "annotated": annotated,
        "by_episode": by_episode,
    }
