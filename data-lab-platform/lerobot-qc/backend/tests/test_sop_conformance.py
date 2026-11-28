"""SOP step conformance: cycle split, alignment, violation classification, takt summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sop_conformance import SopTemplateError, check_episode, load_template, summarize

STEPS = ["reach", "grasp", "lift", "place", "release"]


def tpl(**over):
    data = {"sop_id": "t", "steps": [{"id": s} for s in STEPS], "ignore_labels": ["idle"]}
    data.update(over)
    return load_template(data)


def timeline(labels, start=5.0, dur=2.0):
    """Back-to-back segments; tuples (label, duration) override the default duration."""
    out, t = [], start
    for item in labels:
        label, d = item if isinstance(item, tuple) else (item, dur)
        out.append({"label": label, "start": t, "end": t + d})
        t += d
    return out


def kinds(res):
    return [(v["type"], v["step"]) for c in res["cycles"] for v in c["violations"]]


def test_clean_cycle_is_conformant():
    res = check_episode(timeline(["idle"] + STEPS + ["idle"], start=0.0), tpl())
    assert res["cycle_count"] == 1 and res["conformant"]
    assert res["cycles"][0]["step_durations_s"]["lift"] == 2.0


def test_unsorted_segments_and_adjacent_duplicates_are_normalised():
    segs = timeline(STEPS)
    segs.insert(0, segs.pop())  # stored out of order, like the real annotation files
    segs.append({"label": "lift", "start": segs[3]["end"], "end": segs[3]["end"]})  # zero-length jitter
    res = check_episode(segs, tpl(min_segment_s=0.2))
    assert res["conformant"], kinds(res)


def test_missing_step():
    res = check_episode(timeline(["reach", "grasp", "place", "release"]), tpl())
    assert kinds(res) == [("missing_step", "lift")]
    v = res["cycles"][0]["violations"][0]
    assert (v["after_step"], v["before_step"]) == ("grasp", "place")
    assert (v["expected_from"], v["expected_to"]) == (9.0, 9.0)


def test_optional_step_may_be_skipped():
    t = tpl(steps=[{"id": s, "required": s != "lift"} for s in STEPS])
    assert check_episode(timeline(["reach", "grasp", "place", "release"]), t)["conformant"]


def test_out_of_order_reported_once_not_as_missing():
    res = check_episode(timeline(["reach", "lift", "grasp", "place", "release"]), tpl())
    found = kinds(res)
    assert len(found) == 1 and found[0][0] == "out_of_order"
    assert found[0][1] in {"lift", "grasp"}


def test_rework_is_repeated_step():
    res = check_episode(timeline(["reach", "grasp", "lift", "grasp", "lift", "place", "release"]), tpl())
    assert sorted(kinds(res)) == [("repeated_step", "grasp"), ("repeated_step", "lift")]


def test_unknown_label():
    res = check_episode(timeline(["reach", "grasp", "wave", "lift", "place", "release"]), tpl())
    assert kinds(res) == [("unknown_step", "wave")]


def test_multiple_cycles_split_on_release_and_idle_ignored():
    labels = STEPS + ["idle"] + STEPS + ["idle", "reach", "grasp", "place", "release"]
    res = check_episode(timeline(labels), tpl())
    assert res["cycle_count"] == 3
    assert [c["conformant"] for c in res["cycles"]] == [True, True, False]
    assert kinds(res) == [("missing_step", "lift")]


def test_new_cycle_when_first_step_reappears_without_release():
    labels = ["reach", "grasp", "lift", "place", "reach", "grasp", "lift", "place", "release"]
    res = check_episode(timeline(labels), tpl())
    assert res["cycle_count"] == 2
    assert kinds(res) == [("missing_step", "release")]


def test_stray_early_step_stays_in_cycle():
    # Seen in real data (431132 ep 15): a pre_grasp before reach must not split off a fragment
    # cycle that reports every other step as missing.
    res = check_episode([{"label": "idle", "start": 0, "end": 5}] + timeline(["grasp"] + STEPS), tpl())
    assert res["cycle_count"] == 1
    assert kinds(res) == [("repeated_step", "grasp")]


def test_truncated_edges_hide_missing_prefix_and_suffix():
    # Recording starts mid-cycle (at lift) and ends mid-cycle (after grasp).
    labels = ["lift", "place", "release", "idle"] + STEPS + ["idle", "reach", "grasp"]
    res = check_episode(timeline(labels, start=0.0), tpl())
    assert res["conformant"], kinds(res)
    assert [c["truncated"] for c in res["cycles"]] == [True, False, True]
    summary = summarize({"0": res}, tpl())
    assert summary["complete_cycles"] == 1 and summary["step_duration"]["reach"]["n"] == 1


def test_missing_prefix_away_from_edge_is_a_violation():
    res = check_episode([{"label": "idle", "start": 0, "end": 5}] + timeline(["lift", "place", "release"]), tpl())
    assert sorted(kinds(res)) == [("missing_step", "grasp"), ("missing_step", "reach")]


def test_step_and_cycle_time_limits():
    t = tpl(
        steps=[{"id": s, "max_s": 3 if s == "lift" else None, "min_s": 1 if s == "grasp" else None} for s in STEPS],
        cycle_max_s=9,
    )
    res = check_episode(timeline(["reach", ("grasp", 0.5), ("lift", 4.0), "place", "release"]), t)
    assert sorted(kinds(res)) == [("cycle_too_slow", None), ("step_too_fast", "grasp"), ("step_too_slow", "lift")]


def test_summary_rates_and_counts():
    t = tpl()
    results = {
        "0": check_episode(timeline(STEPS), t),
        "1": check_episode(timeline(["reach", "grasp", "place", "release"]), t),
    }
    s = summarize(results, t)
    assert s["cycles"] == 2 and s["conformant_cycles"] == 1 and s["conformance_rate"] == 0.5
    assert s["violation_counts"] == {"missing_step": 1}
    assert s["step_duration"]["lift"]["n"] == 1


def test_empty_episode_is_not_conformant():
    res = check_episode([{"label": "idle", "start": 0, "end": 10}], tpl())
    assert res["cycle_count"] == 0 and not res["conformant"]


@pytest.mark.parametrize(
    "bad",
    [
        {"steps": []},
        {"steps": [{"id": "a"}, {"id": "a"}]},
        {"steps": [{"id": "a", "min_s": 5, "max_s": 1}]},
        {"steps": [{"id": "a"}], "ignore_labels": ["a"]},
        {"steps": [{"id": "a", "required": False}]},
        {"steps": [{"id": "a", "max_s": "fast"}]},
    ],
)
def test_template_validation(bad):
    with pytest.raises(SopTemplateError):
        load_template(bad)


def test_cli_on_annotation_file(tmp_path: Path, capsys):
    from tools.sop_check import main

    ann = tmp_path / "lerobot_annotations.json"
    ann.write_text(json.dumps({"episodes": {"0": {"subtasks": timeline(STEPS)}, "1": {"subtasks": []}}}))
    sop = Path(__file__).resolve().parents[1] / "sop_templates" / "box_transport_v1.sop.json"
    custom = tmp_path / "t.sop.json"
    custom.write_text(json.dumps({"sop_id": "t", "steps": STEPS, "ignore_labels": ["idle"]}))
    out = tmp_path / "r.json"
    assert main(["--annotations", str(ann), "--sop", str(custom), "--json", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["summary"]["conformant_cycles"] == 1 and list(report["episodes"]) == ["0"]
    assert load_template(json.loads(sop.read_text())).sop_id == "box_transport_v1"
    assert "conformant cycles: 1/1" in capsys.readouterr().out


def test_recheck_rows_widen_zero_gap_window():
    from tools.sop_check import recheck_rows

    t = tpl()
    results = {
        "0": check_episode(timeline(STEPS), t),  # place takes 2 s -> p90 baseline
        "1": check_episode(timeline(["reach", "grasp", "lift", ("place", 4.0), "release"]), t),
        "2": check_episode(timeline(["reach", "grasp", "lift", "release"]), t),  # place absorbed
    }
    rows = recheck_rows(results, summarize(results, t))
    assert len(rows) == 1
    row = rows[0]
    assert (row["episode"], row["type"], row["step"]) == ("2", "missing_step", "place")
    assert (row["after_step"], row["before_step"]) == ("lift", "release")
    assert row["look_to_s"] == 11.0 and 7.0 < row["look_from_s"] < 11.0
