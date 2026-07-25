#!/usr/bin/env python3
"""P0 acceptance audit for UniFranka pick_place full-L2 packages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402

from annotation_jobs_builder import build_annotation_jobs_document  # noqa: E402
from annotation_schema import allowed_label_ids, resolve_annotation_schema, schema_ref  # noqa: E402
from dataset_catalog import datasets_root  # noqa: E402
from episode_progress import build_annotation_progress  # noqa: E402

P0_PACKAGES = ("681496", "681458", "681520")
PICK_LABELS = frozenset(
    {"idle", "reach", "pre_grasp", "contact", "lift", "transport", "place", "release"}
)


def _load_episodes(package_root: Path) -> pd.DataFrame:
    ep_dir = package_root / "meta" / "episodes"
    files = sorted(ep_dir.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No episodes under {ep_dir}")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _load_annotations(package_root: Path) -> dict[int, dict]:
    path = package_root / "meta" / "lerobot_annotations.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in (data.get("episodes") or {}).items()}


def audit_package(
    *,
    collection_dir: Path,
    package_id: str,
    jobs_by_pkg: dict[str, dict],
    require_complete: bool,
) -> dict:
    package_root = collection_dir / package_id
    schema = resolve_annotation_schema(package_root)
    labels = allowed_label_ids(schema)
    annotations = _load_annotations(package_root)
    episodes_df = _load_episodes(package_root)
    episode_indices = [int(x) for x in episodes_df["episode_index"].tolist()]
    progress = build_annotation_progress(annotations, episode_indices, schema)

    bad_labels: list[tuple[int, str]] = []
    missing_outcome = 0
    for ep_idx, ann in annotations.items():
        subs = ann.get("subtasks") or []
        if subs and ann.get("outcome") not in {"success", "fail", "partial"}:
            missing_outcome += 1
        for seg in subs:
            lab = str(seg.get("label") or "")
            if lab and lab not in PICK_LABELS:
                bad_labels.append((ep_idx, lab))

    job = jobs_by_pkg.get(package_id, {})
    checks = {
        "schema_pick_place_v1": schema.get("schema_id") == "pick_place_v1",
        "schema_ref_ok": schema_ref(schema) == "pick_place_v1@1",
        "labels_8_phase": labels == PICK_LABELS,
        "no_cycle_fields": not schema.get("cycle_fields"),
        "job_status_open": job.get("status") == "open",
        "annotations_file_exists": (package_root / "meta" / "lerobot_annotations.json").is_file(),
        "complete_count": progress.get("complete", 0),
        "partial_count": progress.get("partial", 0),
        "total_episodes": progress.get("total", len(episode_indices)),
        "complete_pct": progress.get("complete_pct", 0),
        "invalid_labels": len(bad_labels),
        "missing_outcome_on_annotated": missing_outcome,
    }

    infra_pass = all(
        checks[k]
        for k in (
            "schema_pick_place_v1",
            "schema_ref_ok",
            "labels_8_phase",
            "no_cycle_fields",
            "job_status_open",
        )
    )
    annotation_pass = (
        checks["annotations_file_exists"]
        and checks["complete_pct"] == 100
        and checks["invalid_labels"] == 0
        and checks["missing_outcome_on_annotated"] == 0
    )
    package_pass = infra_pass and (annotation_pass if require_complete else True)

    return {
        "package_id": package_id,
        "checks": checks,
        "infra_pass": infra_pass,
        "annotation_pass": annotation_pass,
        "package_pass": package_pass,
        "bad_labels_sample": bad_labels[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit UniFranka P0 packages")
    parser.add_argument("--collection", default="unifranka")
    parser.add_argument("--datasets-root", type=Path, default=None)
    parser.add_argument(
        "--infra-only",
        action="store_true",
        help="Pass if schema/job ready even when annotations incomplete",
    )
    args = parser.parse_args()

    collection_dir = (args.datasets_root or datasets_root()) / args.collection
    if not collection_dir.is_dir():
        print(f"Collection not found: {collection_dir}", file=sys.stderr)
        return 1

    jobs_doc = build_annotation_jobs_document(collection_dir)
    jobs_by_pkg = {
        j["package_id"]: j
        for j in jobs_doc["jobs"]
        if j.get("scope") == "full" and j.get("package_id") in P0_PACKAGES
    }

    print("P0 ACCEPTANCE AUDIT — unifranka pick_place full L2\n")
    results = []
    for pkg in P0_PACKAGES:
        result = audit_package(
            collection_dir=collection_dir,
            package_id=pkg,
            jobs_by_pkg=jobs_by_pkg,
            require_complete=not args.infra_only,
        )
        results.append(result)
        c = result["checks"]
        print(f"--- {pkg} ---")
        print(f"  infra_pass: {result['infra_pass']}")
        print(f"  annotation_pass: {result['annotation_pass']}")
        print(
            f"  progress: complete={c['complete_count']} partial={c['partial_count']} "
            f"total={c['total_episodes']} ({c['complete_pct']}%)"
        )
        print(f"  annotations_file: {c['annotations_file_exists']}")
        print(f"  invalid_labels: {c['invalid_labels']}")
        print(f"  missing_outcome: {c['missing_outcome_on_annotated']}")
        print(f"  PACKAGE_PASS: {result['package_pass']}\n")

    infra_ready = all(r["infra_pass"] for r in results)
    ann_complete = all(r["annotation_pass"] for r in results)
    overall = infra_ready and (ann_complete if not args.infra_only else True)

    print("=" * 40)
    print(f"INFRA_READY: {infra_ready}")
    print(f"ANNOTATION_COMPLETE: {ann_complete}")
    print(f"P0_OVERALL_PASS: {overall}")
    return 0 if overall else 2


if __name__ == "__main__":
    raise SystemExit(main())
