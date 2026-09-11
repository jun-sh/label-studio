#!/usr/bin/env python3
"""Verify subset export independence on an isolated corpus copy."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DEFAULT_SOURCE = (
    ROOT.parent / "data-storage/ego-delivery/candidates/ego-001-m1-20260911"
)


def _load_export():
    spec = importlib.util.spec_from_file_location("ego_export_delivery", SCRIPTS / "ego-export-delivery.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["ego_export_delivery"] = mod
    spec.loader.exec_module(mod)
    return mod


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_hashes(root: Path) -> list[dict]:
    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        records.append({"path": rel, "sha256": _sha256_file(path), "bytes": path.stat().st_size})
    return records


def verify(source: Path, work_parent: Path, episode_index: int = 2) -> dict:
    export = _load_export()
    work_parent.mkdir(parents=True, exist_ok=True)
    corpus_copy = work_parent / "corpus_copy"
    export_copy = work_parent / "export_subset"
    if corpus_copy.exists():
        shutil.rmtree(corpus_copy)
    shutil.copytree(source, corpus_copy)
    if export_copy.exists():
        shutil.rmtree(export_copy)

    file_records = export.export_episode_slice(
        corpus_root=corpus_copy,
        dest_root=export_copy,
        episode_index=episode_index,
        ann=None,
    )
    manifest = {"files": file_records}
    manifest_path = export_copy / "export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Re-hash on disk and compare manifest entries
    hash_ok = True
    hash_issues = []
    for ent in file_records:
        p = export_copy / ent["path"]
        if not p.is_file():
            hash_ok = False
            hash_issues.append({"path": ent["path"], "issue": "missing"})
            continue
        actual = _sha256_file(p)
        if actual != ent["sha256"]:
            hash_ok = False
            hash_issues.append({"path": ent["path"], "issue": "sha256_mismatch"})

    # Mutate source copy; export files must stay unchanged
    info_copy = corpus_copy / "meta" / "info.json"
    before_export_info_hash = _sha256_file(export_copy / "meta" / "info.json")
    info_copy.write_text(info_copy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    after_mutate = _sha256_file(export_copy / "meta" / "info.json")
    immutable = before_export_info_hash == after_mutate

    lerobot_repo = ROOT.parent.parent / "lerobot"
    loader_py = lerobot_repo / ".venv" / "bin" / "python3"
    if not loader_py.is_file():
        loader_py = Path(sys.executable)
    loader_gate = subprocess.run(
        [
            str(loader_py),
            str(SCRIPTS / "ego-lerobot-loader-gate.py"),
            str(export_copy),
            "--sample",
            "head_mid_tail",
            "--lerobot-repo",
            str(lerobot_repo),
        ],
        capture_output=True,
        text=True,
    )
    loader_ok = loader_gate.returncode == 0

    info = json.loads((export_copy / "meta" / "info.json").read_text(encoding="utf-8"))
    data_files = list((export_copy / "data").rglob("*.parquet"))
    index_ok = bool(data_files)
    if data_files:
        import pandas as pd

        df = pd.read_parquet(data_files[0])
        index_ok = (
            list(df["frame_index"]) == list(range(len(df)))
            and list(df["index"]) == list(range(len(df)))
            and df["episode_index"].nunique() == 1
            and int(df["episode_index"].iloc[0]) == 0
        )

    frozen_manifest = _manifest_hashes(export_copy)
    frozen_path = export_copy / "frozen_manifest.json"
    frozen_path.write_text(json.dumps({"files": frozen_manifest}, indent=2) + "\n", encoding="utf-8")

    ok = hash_ok and immutable and loader_ok and index_ok
    return {
        "ok": ok,
        "source": str(source.resolve()),
        "corpus_copy": str(corpus_copy.resolve()),
        "export_root": str(export_copy.resolve()),
        "episode_index": episode_index,
        "export_manifest_entries": len(file_records),
        "hash_self_consistent": hash_ok,
        "hash_issues": hash_issues,
        "immutable_vs_source_mutation": immutable,
        "loader_gate_ok": loader_ok,
        "loader_gate_stderr": loader_gate.stderr[-500:] if loader_gate.stderr else "",
        "index_semantics_ok": index_ok,
        "subset_info": info.get("ego_export"),
        "frozen_manifest": str(frozen_path.resolve()),
        "frozen_file_count": len(frozen_manifest),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Subset export independence verify")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--episode-index", type=int, default=2)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.source, args.work_dir, episode_index=args.episode_index)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(args.report.resolve())}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
