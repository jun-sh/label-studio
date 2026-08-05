"""Tests for stable QC sidecar path resolution."""

from __future__ import annotations

import json
from pathlib import Path

from qc_store import (
    QcStore,
    canonical_dataset_key,
    legacy_dataset_sidecar_id,
    resolve_sidecar_dir,
)


def test_canonical_dataset_key_ignores_mount_prefix() -> None:
    old = Path("/data/bookduo/01 - UniFranka_Converted_Ver_3_5/681447")
    new = Path("/host-media/BookDuo_21/01 - UniFranka_Converted_Ver_3_5/681447")
    assert canonical_dataset_key(old) == canonical_dataset_key(new)
    assert canonical_dataset_key(old) == "01 - UniFranka_Converted_Ver_3_5/681447"


def test_resolve_sidecar_dir_prefers_richest_legacy_store(tmp_path: Path) -> None:
    sidecar_base = tmp_path / "sidecar"
    sidecar_base.mkdir()
    legacy_root = Path("/data/bookduo/01 - UniFranka_Converted_Ver_3_5/681448")
    rich_dir = sidecar_base / legacy_dataset_sidecar_id(legacy_root)
    rich_dir.mkdir(parents=True)
    (rich_dir / "qc_manifest.json").write_text(
        json.dumps({"dataset_root": str(legacy_root), "reviews": {"0": {}, "1": {}}}),
        encoding="utf-8",
    )

    new_root = Path("/host-media/BookDuo_21/01 - UniFranka_Converted_Ver_3_5/681448")
    empty_dir = sidecar_base / legacy_dataset_sidecar_id(new_root)
    empty_dir.mkdir(parents=True)
    (empty_dir / "qc_manifest.json").write_text(
        json.dumps({"dataset_root": str(new_root), "reviews": {}}),
        encoding="utf-8",
    )

    assert resolve_sidecar_dir(new_root, sidecar_base) == rich_dir


def test_resolve_sidecar_dir_reuses_legacy_store(tmp_path: Path) -> None:
    sidecar_base = tmp_path / "sidecar"
    sidecar_base.mkdir()
    legacy_root = Path("/data/bookduo/01 - UniFranka_Converted_Ver_3_5/681447")
    legacy_dir = sidecar_base / legacy_dataset_sidecar_id(legacy_root)
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "qc_manifest.json").write_text(
        json.dumps(
            {
                "dataset_root": str(legacy_root),
                "reviews": {"0": {"status": "approved"}},
            }
        ),
        encoding="utf-8",
    )

    new_root = Path("/host-media/BookDuo_21/01 - UniFranka_Converted_Ver_3_5/681447")
    resolved = resolve_sidecar_dir(new_root, sidecar_base)
    assert resolved == legacy_dir

    store = QcStore.open(new_root, sidecar_base, operator_id="tester")
    assert store.sidecar_root == legacy_dir
    assert store.get_review(0)["status"] == "approved"
    assert store.manifest["dataset_root"] == str(new_root.resolve())
