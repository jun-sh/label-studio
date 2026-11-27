"""Read-only ops reports: sidecar health and acceptance of existing deliveries."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import qc_ops_report  # noqa: E402
from test_acceptance import _fresh_state, _rebuild, video_dataset  # noqa: E402,F401  (fixtures)
from test_minimal_rebuild import minimal_dataset  # noqa: E402,F401  (fixture)


def _sidecar(root: Path, name: str, manifest: dict) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "qc_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _snapshot(path: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def test_sidecar_report(tmp_path: Path) -> None:
    sidecars = tmp_path / "sidecar"
    data = tmp_path / "disk" / "Converted" / "01 - Franka"
    (data / "pkg1").mkdir(parents=True)
    old_root = str(tmp_path / "disk" / "01 - Franka" / "pkg1")  # same package before the data moved
    _sidecar(
        sidecars,
        "pkg1_new",
        {
            "dataset_root": str(data / "pkg1"),
            "reviews": {"0": {"status": "approved"}, "1": {"status": "rejected"}, "2": {"status": "approved"}},
            "rebuild_jobs": [
                {"job_id": "a", "status": "completed", "output_root": str(tmp_path / "gone")},
                {"job_id": "b", "status": "running"},
            ],
        },
    )
    _sidecar(
        sidecars,
        "pkg1_old",
        {"dataset_root": old_root, "reviews": {"2": {"status": "rejected"}, "7": {"status": "approved"}}},
    )
    _sidecar(sidecars, "pkg2_empty", {"dataset_root": str(data / "pkg2"), "reviews": {}})
    (sidecars / "broken").mkdir()
    (sidecars / "broken" / "qc_manifest.json").write_text("{not json", encoding="utf-8")
    before = _snapshot(sidecars)

    report = qc_ops_report.sidecar_report(sidecars)

    assert report["sidecars"] == 4 and report["packages"] == 2
    assert [u["dir"] for u in report["unreadable"]] == ["broken"]
    assert {m["dir"] for m in report["missing_dataset_root"]} == {"pkg1_old", "pkg2_empty"}
    assert report["empty"] == ["pkg2_empty"]
    assert report["stuck_jobs"] == [{"dir": "pkg1_new", "job_id": "b", "status": "running"}]
    assert [j["job_id"] for j in report["completed_jobs_output_deleted"]] == ["a"]
    [dup] = report["duplicates"]
    assert dup["package"] == "01 - Franka/pkg1"
    assert dup["used"]["dir"] == "pkg1_new" and dup["used"]["reviews"] == 3
    assert dup["ignored"] == [{"dir": "pkg1_old", "dataset_root": old_root, "reviews": 2, "hidden_reviews": 1}]
    assert _snapshot(sidecars) == before  # read-only


def test_delivery_report_checks_latest_delivery(video_dataset, tmp_path: Path) -> None:
    root, sidecars = video_dataset
    deliveries = tmp_path / "deliveries"
    out = deliveries / "source_ds_v1"
    _, store, _ = _rebuild(root, sidecars, out)
    store.register_rebuild_job(
        {"job_id": "j1", "status": "completed", "output_root": str(out), "started_at": "2999-01-01T00:00:00+00:00"}
    )
    (deliveries / "leftover_v0").mkdir()
    before = _snapshot(deliveries)

    report = qc_ops_report.delivery_report(sidecars, deliveries)

    assert report["checked"] == 1 and report["passed"] == 1, report
    [row] = report["deliveries"]
    assert row["delivery_state"] == "current"
    assert row["acceptance"]["video_segments"] == 4
    assert report["unreferenced_delivery_dirs"] == ["leftover_v0"]
    assert _snapshot(deliveries) == before

    # Breaking the delivery is reported and gives a non-zero exit status.
    (out / "meta" / "stats.json").write_text(json.dumps({"action": {"count": [1]}}), encoding="utf-8")
    code = qc_ops_report.main(
        ["deliveries", "--sidecar-root", str(sidecars), "--delivery-root", str(deliveries), "--json", str(tmp_path / "r.json")]
    )
    assert code == 1
    saved = json.loads((tmp_path / "r.json").read_text())
    assert any("stats.json" in p for p in saved["deliveries"][0]["acceptance"]["problems"])


def test_delivery_report_package_filter(video_dataset, tmp_path: Path) -> None:
    root, sidecars = video_dataset
    out = tmp_path / "deliveries" / "source_ds_v1"
    _, store, _ = _rebuild(root, sidecars, out)
    store.register_rebuild_job({"job_id": "j1", "status": "completed", "output_root": str(out)})
    assert qc_ops_report.delivery_report(sidecars, out.parent, package="other")["checked"] == 0
    assert qc_ops_report.delivery_report(sidecars, out.parent, package="source_ds")["checked"] == 1

