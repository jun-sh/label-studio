"""SOP conformance: compare an episode's step timeline against a standard operating procedure.

Input is a list of time segments ``{"start": s, "end": s, "label": step_id}`` (the same shape
embodied-annotate stores in ``meta/lerobot_annotations.json``; it can equally come from an
automatic segmenter). The SOP template lists the steps of one work cycle in order:

    {
      "sop_id": "box_transport_v1",
      "steps": [{"id": "reach", "required": true, "min_s": null, "max_s": 15}, ...],
      "ignore_labels": ["idle"],     # filler between cycles, never a violation
      "min_segment_s": 0.2,          # shorter segments are treated as segmentation jitter
      "cycle_max_s": null,           # optional takt limit for one whole cycle
      "edge_tolerance_s": 1.0        # a cycle this close to the episode edge may be truncated
    }

Pipeline per episode: sort + clean segments -> split into cycles -> align each cycle to the
template with edit distance -> classify the edit operations into violations. Pure Python, no I/O.

Violation types:
    missing_step     required template step absent from the cycle
    out_of_order     step present but not in template order
    repeated_step    step done again after it was already matched (rework)
    unknown_step     label that is not part of the SOP
    step_too_slow    step duration above max_s
    step_too_fast    step duration below min_s
    cycle_too_slow   whole cycle above cycle_max_s
A cycle cut by the start or end of the recording (missing a prefix/suffix of steps and lying
within edge_tolerance_s of the edge) is marked ``truncated``: those hidden steps are not
reported (the spec says: label only what is visible) and it is left out of takt statistics.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

VIOLATION_TYPES = (
    "missing_step",
    "out_of_order",
    "repeated_step",
    "unknown_step",
    "step_too_slow",
    "step_too_fast",
    "cycle_too_slow",
)


class SopTemplateError(ValueError):
    pass


@dataclass(frozen=True)
class SopStep:
    id: str
    required: bool = True
    min_s: float | None = None
    max_s: float | None = None


@dataclass(frozen=True)
class SopTemplate:
    sop_id: str
    steps: tuple[SopStep, ...]
    ignore_labels: frozenset[str] = frozenset()
    min_segment_s: float = 0.0
    cycle_max_s: float | None = None
    edge_tolerance_s: float = 1.0

    @property
    def step_ids(self) -> list[str]:
        return [s.id for s in self.steps]

    def index_of(self, label: str) -> int | None:
        for i, s in enumerate(self.steps):
            if s.id == label:
                return i
        return None


def _opt_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise SopTemplateError(f"{name} must be a number, got {value!r}") from exc
    if out < 0:
        raise SopTemplateError(f"{name} must be >= 0, got {out}")
    return out


def load_template(data: dict[str, Any]) -> SopTemplate:
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise SopTemplateError("SOP template needs a non-empty 'steps' list")
    steps: list[SopStep] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_steps):
        if isinstance(raw, str):
            raw = {"id": raw}
        step_id = str((raw or {}).get("id") or "").strip()
        if not step_id:
            raise SopTemplateError(f"step #{i} has no id")
        if step_id in seen:
            raise SopTemplateError(f"duplicate step id {step_id!r}")
        seen.add(step_id)
        min_s = _opt_float(raw.get("min_s"), f"{step_id}.min_s")
        max_s = _opt_float(raw.get("max_s"), f"{step_id}.max_s")
        if min_s is not None and max_s is not None and min_s > max_s:
            raise SopTemplateError(f"{step_id}: min_s {min_s} > max_s {max_s}")
        steps.append(SopStep(step_id, bool(raw.get("required", True)), min_s, max_s))
    ignore = frozenset(str(x) for x in data.get("ignore_labels") or [])
    overlap = ignore & seen
    if overlap:
        raise SopTemplateError(f"labels both ignored and SOP steps: {sorted(overlap)}")
    if not any(s.required for s in steps):
        raise SopTemplateError("SOP template needs at least one required step")
    edge = _opt_float(data.get("edge_tolerance_s"), "edge_tolerance_s")
    return SopTemplate(
        sop_id=str(data.get("sop_id") or "sop"),
        steps=tuple(steps),
        ignore_labels=ignore,
        min_segment_s=_opt_float(data.get("min_segment_s"), "min_segment_s") or 0.0,
        cycle_max_s=_opt_float(data.get("cycle_max_s"), "cycle_max_s"),
        edge_tolerance_s=1.0 if edge is None else edge,
    )


@dataclass
class Segment:
    label: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def clean_segments(raw: list[dict[str, Any]], template: SopTemplate) -> list[Segment]:
    """Sort by time, drop ignored labels and jitter, merge adjacent runs of the same label."""
    segs = []
    for item in raw or []:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        label = str(item.get("label") or "")
        if not label or end < start:
            continue
        segs.append(Segment(label, start, end))
    segs.sort(key=lambda s: (s.start, s.end))
    kept = [
        s
        for s in segs
        if s.label not in template.ignore_labels and s.duration >= template.min_segment_s
    ]
    merged: list[Segment] = []
    for s in kept:
        if merged and merged[-1].label == s.label:
            merged[-1].end = max(merged[-1].end, s.end)
        else:
            merged.append(Segment(s.label, s.start, s.end))
    return merged


def split_cycles(segs: list[Segment], template: SopTemplate) -> list[list[Segment]]:
    """A new cycle starts when anything follows the last step, or when the first step reappears
    after the current cycle has covered at least half of the SOP steps. The half rule keeps a
    stray early step (e.g. pre_grasp before reach) inside the cycle instead of splitting off a
    one-segment fragment that would be reported as a cycle missing every other step.

    Known limit: if both the last step of one cycle and the first step of the next are missing,
    the two cycles merge and show up as repeated / out-of-order steps (still flagged, just
    attributed to one cycle).
    """
    first, last = template.steps[0].id, template.steps[-1].id
    sop_ids = set(template.step_ids)
    half = (len(sop_ids) + 1) // 2
    cycles: list[list[Segment]] = []
    current: list[Segment] = []
    for s in segs:
        if current:
            closed = any(c.label == last for c in current)
            covered = {c.label for c in current} & sop_ids
            restarts = s.label == first and len(covered - {first}) >= half
            if closed or restarts:
                cycles.append(current)
                current = []
        current.append(s)
    if current:
        cycles.append(current)
    return cycles


def _align(observed: list[str], expected: list[str]) -> list[tuple[str, int | None, int | None]]:
    """Levenshtein alignment; ops are ('match', i, j), ('extra', i, None), ('missing', None, j)."""
    n, m = len(observed), len(expected)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1)
            if observed[i - 1] == expected[j - 1]:
                best = min(best, dp[i - 1][j - 1])
            dp[i][j] = best
    ops: list[tuple[str, int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and observed[i - 1] == expected[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            ops.append(("match", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif j > 0 and dp[i][j] == dp[i][j - 1] + 1:
            ops.append(("missing", None, j - 1))
            j -= 1
        else:
            ops.append(("extra", i - 1, None))
            i -= 1
    ops.reverse()
    return ops


def _violation(kind: str, step: str, segment: Segment | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": kind, "step": step}
    if segment is not None:
        out["start"] = round(segment.start, 3)
        out["end"] = round(segment.end, 3)
    out.update(extra)
    return out


def check_cycle(
    cycle: list[Segment],
    template: SopTemplate,
    *,
    truncated_start: bool = False,
    truncated_end: bool = False,
) -> dict[str, Any]:
    expected = template.step_ids
    observed = [s.label for s in cycle]
    ops = _align(observed, expected)

    matched_j = {j for op, _, j in ops if op == "match"}
    missing_j = sorted(j for op, _, j in ops if op == "missing")
    # A truncated recording hides a prefix (or suffix) of the cycle: skip those missing steps.
    first_matched = min(matched_j) if matched_j else len(expected)
    last_matched = max(matched_j) if matched_j else -1
    hidden: set[int] = set()
    if truncated_start:
        hidden |= {j for j in missing_j if j < first_matched}
    if truncated_end:
        hidden |= {j for j in missing_j if j > last_matched}

    violations: list[dict[str, Any]] = []
    present_labels = set(observed)
    step_durations: dict[str, float] = {}

    for pos, (op, i, j) in enumerate(ops):
        if op == "match":
            seg = cycle[i]
            step = template.steps[j]
            step_durations[step.id] = seg.duration
            if step.max_s is not None and seg.duration > step.max_s:
                violations.append(
                    _violation("step_too_slow", step.id, seg, duration_s=round(seg.duration, 3), limit_s=step.max_s)
                )
            if step.min_s is not None and seg.duration < step.min_s:
                violations.append(
                    _violation("step_too_fast", step.id, seg, duration_s=round(seg.duration, 3), limit_s=step.min_s)
                )
        elif op == "extra":
            seg = cycle[i]
            idx = template.index_of(seg.label)
            if idx is None:
                violations.append(_violation("unknown_step", seg.label, seg))
            elif idx in matched_j:
                violations.append(_violation("repeated_step", seg.label, seg))
            else:
                violations.append(_violation("out_of_order", seg.label, seg))
        else:  # missing
            step = template.steps[j]
            # The step exists elsewhere in the cycle -> already reported as out_of_order.
            if j in hidden or not step.required or step.id in present_labels:
                continue
            # Where the step should have been: between the neighbouring observed segments.
            before = next((cycle[ops[p][1]] for p in range(pos - 1, -1, -1) if ops[p][1] is not None), None)
            after = next((cycle[ops[p][1]] for p in range(pos + 1, len(ops)) if ops[p][1] is not None), None)
            extra: dict[str, Any] = {}
            if before is not None:
                extra["after_step"] = before.label
                extra["expected_from"] = round(before.end, 3)
            if after is not None:
                extra["before_step"] = after.label
                extra["expected_to"] = round(after.start, 3)
            violations.append(_violation("missing_step", step.id, **extra))

    # Touching the recording edge only matters when steps are actually cut off.
    truncated = bool(hidden)
    start, end = cycle[0].start, cycle[-1].end
    duration = end - start
    if template.cycle_max_s is not None and duration > template.cycle_max_s and not truncated:
        violations.append(
            {"type": "cycle_too_slow", "step": None, "duration_s": round(duration, 3), "limit_s": template.cycle_max_s}
        )
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "duration_s": round(duration, 3),
        "sequence": observed,
        "truncated": truncated,
        "conformant": not violations,
        "step_durations_s": {k: round(v, 3) for k, v in step_durations.items()},
        "violations": violations,
    }


def check_episode(
    raw_segments: list[dict[str, Any]],
    template: SopTemplate,
    *,
    episode_start: float | None = None,
    episode_end: float | None = None,
) -> dict[str, Any]:
    """Check one episode. Episode bounds default to the span of all (uncleaned) segments."""
    times = [
        (float(s["start"]), float(s["end"]))
        for s in raw_segments or []
        if isinstance(s, dict) and "start" in s and "end" in s
    ]
    if episode_start is None:
        episode_start = min((t[0] for t in times), default=0.0)
    if episode_end is None:
        episode_end = max((t[1] for t in times), default=0.0)

    segs = clean_segments(raw_segments, template)
    cycles = split_cycles(segs, template)
    tol = template.edge_tolerance_s
    results = []
    for k, cycle in enumerate(cycles):
        results.append(
            check_cycle(
                cycle,
                template,
                truncated_start=k == 0 and cycle[0].start - episode_start <= tol,
                truncated_end=k == len(cycles) - 1 and episode_end - cycle[-1].end <= tol,
            )
        )
    counts: dict[str, int] = {}
    for c in results:
        for v in c["violations"]:
            counts[v["type"]] = counts.get(v["type"], 0) + 1
    return {
        "cycles": results,
        "cycle_count": len(results),
        "conformant": bool(results) and all(c["conformant"] for c in results),
        "violation_counts": counts,
    }


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


@dataclass
class _Agg:
    episodes: int = 0
    cycles: int = 0
    conformant_cycles: int = 0
    complete_cycles: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    durations: dict[str, list[float]] = field(default_factory=dict)
    cycle_durations: list[float] = field(default_factory=list)


def summarize(episode_results: dict[Any, dict[str, Any]], template: SopTemplate) -> dict[str, Any]:
    """Fleet view: conformance rate, violations by type, takt stats per step (complete cycles only)."""
    agg = _Agg()
    for res in episode_results.values():
        agg.episodes += 1
        for c in res["cycles"]:
            agg.cycles += 1
            agg.conformant_cycles += int(c["conformant"])
            for v in c["violations"]:
                agg.counts[v["type"]] = agg.counts.get(v["type"], 0) + 1
            if c["truncated"]:
                continue
            agg.complete_cycles += 1
            agg.cycle_durations.append(c["duration_s"])
            for step, d in c["step_durations_s"].items():
                agg.durations.setdefault(step, []).append(d)

    def stats(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"n": 0}
        return {
            "n": len(values),
            "mean_s": round(statistics.fmean(values), 3),
            "p50_s": round(_quantile(values, 0.5), 3),
            "p90_s": round(_quantile(values, 0.9), 3),
            "max_s": round(max(values), 3),
        }

    return {
        "sop_id": template.sop_id,
        "episodes": agg.episodes,
        "cycles": agg.cycles,
        "complete_cycles": agg.complete_cycles,
        "conformant_cycles": agg.conformant_cycles,
        "conformance_rate": round(agg.conformant_cycles / agg.cycles, 4) if agg.cycles else None,
        "violation_counts": {k: agg.counts[k] for k in VIOLATION_TYPES if k in agg.counts},
        "cycle_duration": stats(agg.cycle_durations),
        "step_duration": {s: stats(agg.durations.get(s, [])) for s in template.step_ids},
    }
