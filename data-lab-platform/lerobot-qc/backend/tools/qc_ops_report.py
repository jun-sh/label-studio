#!/usr/bin/env python3
"""Read-only operational reports for the LeRobot QC tool. Changes nothing on disk except --json.

Run inside the QC container, where the paths stored in sidecars resolve:

    docker exec -w /app/backend datalab-qc-1 python tools/qc_ops_report.py sidecars
    docker exec -w /app/backend datalab-qc-1 python tools/qc_ops_report.py deliveries [--package 681448]

sidecars    health of the QC sidecars: dataset roots that no longer exist, several sidecars
            for one package (and reviews hidden in the ones the UI does not pick), empty
            sidecars, jobs left queued/running, completed jobs whose output was deleted.
deliveries  run the delivery acceptance check on the latest existing delivery of every
            package; list delivery directories no completed job refers to.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from acceptance_service import check_delivery  # noqa: E402
from qc_store import canonical_dataset_key, manifest_delivery_status  # noqa: E402


def load_sidecars(sidecar_root: Path) -> list[dict[str, Any]]:
    """[{dir, manifest}] for every readable sidecar manifest; unreadable ones get an error entry."""
    found = []
    for path in sorted(sidecar_root.glob("*/qc_manifest.json")):
        try:
            found.append({"dir": path.parent.name, "manifest": json.loads(path.read_text(encoding="utf-8"))})
        except (OSError, json.JSONDecodeError) as exc:
            found.append({"dir": path.parent.name, "manifest": None, "error": str(exc)})
    return found


def _key(manifest: dict[str, Any]) -> str | None:
    root = manifest.get("dataset_root")
    return canonical_dataset_key(Path(str(root)), resolve=False) if root else None


def _winner(entries: list[dict[str, Any]]) -> dict[str, Any]:
    # Same rule as overview_service.index_sidecars / qc_store.resolve_sidecar_dir: most reviews wins.
    return max(entries, key=lambda e: (len(e["manifest"].get("reviews") or {}), e["dir"]))


def sidecar_report(sidecar_root: Path) -> dict[str, Any]:
    sidecars = load_sidecars(sidecar_root)
    unreadable = [{"dir": s["dir"], "error": s["error"]} for s in sidecars if s["manifest"] is None]
    readable = [s for s in sidecars if s["manifest"] is not None]

    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in readable:
        key = _key(s["manifest"])
        if key:
            by_key[key].append(s)

    missing_root, empty, stuck_jobs, deleted_outputs = [], [], [], []
    for s in readable:
        m = s["manifest"]
        root = str(m.get("dataset_root") or "")
        reviews = len(m.get("reviews") or {})
        jobs = m.get("rebuild_jobs") or []
        if root and not Path(root).is_dir():
            missing_root.append({"dir": s["dir"], "dataset_root": root, "reviews": reviews})
        if not reviews and not (m.get("instruction_overrides") or {}) and not jobs:
            empty.append(s["dir"])
        for job in jobs:
            if job.get("status") in ("queued", "running"):
                stuck_jobs.append({"dir": s["dir"], "job_id": job.get("job_id"), "status": job.get("status")})
            elif job.get("status") == "completed" and job.get("output_root") and not Path(job["output_root"]).is_dir():
                deleted_outputs.append({"dir": s["dir"], "job_id": job.get("job_id"), "output_root": job["output_root"]})

    duplicates = []
    for key, entries in sorted(by_key.items()):
        if len(entries) < 2:
            continue
        winner = _winner(entries)
        shown = set((winner["manifest"].get("reviews") or {}).keys())
        losers = []
        for e in entries:
            if e is winner:
                continue
            reviews = e["manifest"].get("reviews") or {}
            losers.append(
                {
                    "dir": e["dir"],
                    "dataset_root": e["manifest"].get("dataset_root"),
                    "reviews": len(reviews),
                    # Reviewed only here: hidden from the UI, which reads the winner.
                    "hidden_reviews": len(set(reviews) - shown),
                }
            )
        duplicates.append(
            {
                "package": key,
                "used": {"dir": winner["dir"], "dataset_root": winner["manifest"].get("dataset_root"), "reviews": len(shown)},
                "ignored": losers,
            }
        )

    return {
        "sidecar_root": str(sidecar_root),
        "sidecars": len(sidecars),
        "packages": len(by_key),
        "unreadable": unreadable,
        "missing_dataset_root": missing_root,
        "duplicates": duplicates,
        "empty": empty,
        "stuck_jobs": stuck_jobs,
        "completed_jobs_output_deleted": deleted_outputs,
    }


def _source_root(manifest: dict[str, Any], key: str, packages_by_key: dict[str, str]) -> Path | None:
    root = Path(str(manifest.get("dataset_root") or ""))
    if root.is_dir():
        return root
    alt = packages_by_key.get(key)
    return Path(alt) if alt else None


def delivery_report(sidecar_root: Path, delivery_root: Path, package: str | None = None) -> dict[str, Any]:
    readable = [s for s in load_sidecars(sidecar_root) if s["manifest"] is not None]
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in readable:
        key = _key(s["manifest"])
        if key:
            by_key[key].append(s)
    # A sidecar whose stored root moved can still be checked against any sidecar's root that exists.
    existing_roots = {
        key: str(s["manifest"]["dataset_root"])
        for key, entries in by_key.items()
        for s in entries
        if Path(str(s["manifest"]["dataset_root"])).is_dir()
    }

    referenced: set[str] = set()
    results = []
    for key, entries in sorted(by_key.items()):
        for e in entries:
            for job in e["manifest"].get("rebuild_jobs") or []:
                if job.get("status") == "completed" and job.get("output_root"):
                    referenced.add(Path(job["output_root"]).name)
        if package and not key.endswith(f"/{package}") and key != package:
            continue
        manifest = _winner(entries)["manifest"]
        delivery = manifest_delivery_status(manifest)
        if delivery["state"] in ("never_built", "missing"):
            continue
        row: dict[str, Any] = {
            "package": key,
            "output_root": delivery["output_root"],
            "delivery_state": delivery["state"],
            "kept_episodes": delivery.get("kept_episodes"),
            "recorded_acceptance": (delivery.get("acceptance") or {}).get("ok"),
        }
        source = _source_root(manifest, key, existing_roots)
        if source is None:
            row["acceptance"] = {"ok": False, "problems": ["source dataset not found"]}
        else:
            removed = {int(k) for k, v in (manifest.get("reviews") or {}).items() if v.get("status") == "rejected"}
            started = time.time()
            row["acceptance"] = check_delivery(
                Path(delivery["output_root"]),
                source,
                removed=removed,
                overrides=dict(manifest.get("instruction_overrides") or {}),
            )
            row["seconds"] = round(time.time() - started)
        if delivery["state"] == "stale":
            row["note"] = "QC changed after this delivery was built; mismatches against current decisions are expected"
        print(f"{key}: {'OK' if row['acceptance']['ok'] else 'FAILED'} {row['acceptance']['problems'][:3]}", file=sys.stderr, flush=True)
        results.append(row)

    unreferenced = sorted(
        p.name for p in delivery_root.iterdir() if p.is_dir() and p.name not in referenced
    ) if delivery_root.is_dir() and not package else []
    return {
        "delivery_root": str(delivery_root),
        "checked": len(results),
        "passed": sum(1 for r in results if r["acceptance"]["ok"]),
        "deliveries": results,
        "unreferenced_delivery_dirs": unreferenced,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", choices=("sidecars", "deliveries"))
    parser.add_argument("--sidecar-root", type=Path, default=Path(os.environ.get("LEROBOT_QC_SIDECAR", "/data/qc-sidecar")))
    parser.add_argument("--delivery-root", type=Path, default=Path(os.environ.get("LEROBOT_QC_DELIVERY", "/data/qc-delivery")))
    parser.add_argument("--package", help="deliveries: only this package id (e.g. 681448)")
    parser.add_argument("--json", type=Path, help="also write the full report to this file")
    args = parser.parse_args(argv)

    if args.report == "sidecars":
        report = sidecar_report(args.sidecar_root)
    else:
        report = delivery_report(args.sidecar_root, args.delivery_root, args.package)
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    failed = report.get("checked", 0) - report.get("passed", 0) if args.report == "deliveries" else 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
