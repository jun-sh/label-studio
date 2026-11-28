#!/usr/bin/env python3
"""Check step timelines against an SOP template. Read-only; writes only --json.

    python tools/sop_check.py \
        --annotations <dataset>/meta/lerobot_annotations.json \
        --sop sop_templates/box_transport_v1.sop.json [--json report.json] [--episodes 0,3,7]

--annotations takes embodied-annotate's lerobot_annotations.json ({"episodes": {idx: {"subtasks":
[...]}}}) or a plain {idx: [segments]} map, e.g. the output of an automatic segmenter.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sop_conformance import check_episode, load_template, summarize  # noqa: E402


def load_timelines(path: Path) -> dict[str, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    episodes = data.get("episodes", data) if isinstance(data, dict) else {}
    out: dict[str, list[dict]] = {}
    for key, value in episodes.items():
        segments = value.get("subtasks") if isinstance(value, dict) else value
        if isinstance(segments, list) and segments:
            out[str(key)] = segments
    return out


RECHECK_FIELDS = [
    "episode", "cycle", "type", "step", "look_from_s", "look_to_s",
    "after_step", "before_step", "duration_s", "limit_s",
]


def recheck_rows(results: dict[str, dict], summary: dict) -> list[dict]:
    """One row per violation with the time window an annotator should open.

    A missing step whose neighbours touch (zero-width gap) was most likely absorbed into the
    preceding segment, so the window reaches back by that step's p90 duration in this batch.
    """
    rows = []
    for ep, res in results.items():
        for ci, cycle in enumerate(res["cycles"]):
            for v in cycle["violations"]:
                if v["type"] == "missing_step":
                    hi = v.get("expected_to", v.get("expected_from"))
                    lo = v.get("expected_from", hi)
                    p90 = (summary["step_duration"].get(v["step"]) or {}).get("p90_s")
                    if lo is not None and hi is not None and hi - lo < 0.5 and p90:
                        lo = max(cycle["start"], lo - p90)
                else:
                    lo, hi = v.get("start", cycle["start"]), v.get("end", cycle["end"])
                rows.append({
                    "episode": ep,
                    "cycle": ci,
                    "type": v["type"],
                    "step": v["step"] or "",
                    "look_from_s": "" if lo is None else round(lo, 2),
                    "look_to_s": "" if hi is None else round(hi, 2),
                    "after_step": v.get("after_step", ""),
                    "before_step": v.get("before_step", ""),
                    "duration_s": v.get("duration_s", ""),
                    "limit_s": v.get("limit_s", ""),
                })
    return rows


def _sort_key(key: str):
    return (0, int(key)) if key.isdigit() else (1, key)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotations", required=True, type=Path)
    ap.add_argument("--sop", required=True, type=Path)
    ap.add_argument("--json", type=Path, help="write the full per-cycle report here")
    ap.add_argument("--episodes", help="comma-separated episode keys to check (default: all)")
    ap.add_argument("--recheck-csv", type=Path, help="write one row per violation for annotator review")
    args = ap.parse_args(argv)

    template = load_template(json.loads(args.sop.read_text(encoding="utf-8")))
    timelines = load_timelines(args.annotations)
    if args.episodes:
        wanted = {e.strip() for e in args.episodes.split(",") if e.strip()}
        timelines = {k: v for k, v in timelines.items() if k in wanted}
    if not timelines:
        print("no episodes with step segments found", file=sys.stderr)
        return 2

    results = {k: check_episode(timelines[k], template) for k in sorted(timelines, key=_sort_key)}
    summary = summarize(results, template)

    print(f"SOP {summary['sop_id']}: {summary['episodes']} episodes, {summary['cycles']} cycles "
          f"({summary['complete_cycles']} complete)")
    rate = summary["conformance_rate"]
    print(f"conformant cycles: {summary['conformant_cycles']}/{summary['cycles']}"
          + (f" ({rate:.1%})" if rate is not None else ""))
    for kind, n in summary["violation_counts"].items():
        print(f"  {kind:<15} {n}")
    print("step takt (complete cycles):  n   mean    p50    p90    max")
    for step, st in summary["step_duration"].items():
        if st["n"]:
            print(f"  {step:<14} {st['n']:>5} {st['mean_s']:>6.2f} {st['p50_s']:>6.2f} "
                  f"{st['p90_s']:>6.2f} {st['max_s']:>6.2f}")
        else:
            print(f"  {step:<14}     0")
    flagged = [(k, r) for k, r in results.items() if not r["conformant"]]
    if flagged:
        print("non-conformant episodes:")
        for key, res in flagged:
            items = [
                f"{v['type']}:{v['step']}" + (f"@{v['start']}s" if "start" in v else "")
                for c in res["cycles"]
                for v in c["violations"]
            ]
            print(f"  ep {key}: " + ", ".join(items[:8]) + (" ..." if len(items) > 8 else ""))

    if args.recheck_csv:
        rows = recheck_rows(results, summary)
        with args.recheck_csv.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=RECHECK_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"recheck list ({len(rows)} rows) written to {args.recheck_csv}")

    if args.json:
        args.json.write_text(
            json.dumps({"summary": summary, "episodes": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
