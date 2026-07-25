#!/usr/bin/env python3
"""Generate collection-level annotation.jobs.json (annotation_job_spec@1.0 · D2)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from annotation_jobs_builder import (  # noqa: E402
    DEFAULT_SAMPLE_COUNT,
    DEFAULT_SAMPLE_SEED,
    build_annotation_jobs_document,
    write_annotation_jobs,
)
from dataset_catalog import datasets_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate annotation.jobs.json for a collection")
    parser.add_argument(
        "collection",
        nargs="?",
        default="unifranka",
        help="Collection id under datasets root (default: unifranka)",
    )
    parser.add_argument(
        "--datasets-root",
        type=Path,
        default=None,
        help="Override LEROBOT_ANNOTATE_DATASETS root",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: <collection>/annotation.jobs.json)",
    )
    parser.add_argument("--sample-count", type=int, default=DEFAULT_SAMPLE_COUNT)
    parser.add_argument("--sample-seed", type=int, default=DEFAULT_SAMPLE_SEED)
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing")
    args = parser.parse_args()

    root = args.datasets_root or datasets_root()
    collection_dir = root / args.collection
    if not collection_dir.is_dir():
        print(f"Collection not found: {collection_dir}", file=sys.stderr)
        return 1

    if args.dry_run:
        doc = build_annotation_jobs_document(
            collection_dir,
            sample_count=args.sample_count,
            sample_seed=args.sample_seed,
        )
        print(f"collection={doc['collection_id']} jobs={len(doc['jobs'])}")
        for job in doc["jobs"]:
            ep_n = len(job.get("episode_indices") or [])
            scope = job.get("scope")
            extra = f" {ep_n} ep" if scope == "sample" else " full"
            print(
                f"  [{job.get('status')}] p{job.get('priority'):>3} "
                f"{job.get('job_id')}{extra}"
            )
        return 0

    out = write_annotation_jobs(
        collection_dir,
        output_path=args.output,
        sample_count=args.sample_count,
        sample_seed=args.sample_seed,
    )
    doc = build_annotation_jobs_document(collection_dir)
    print(f"Wrote {len(doc['jobs'])} jobs → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
