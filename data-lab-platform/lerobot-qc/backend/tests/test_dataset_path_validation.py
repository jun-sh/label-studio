"""Tests for dataset path allowlist validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from dataset_catalog import validate_dataset_path


def test_validate_dataset_path_rejects_outside_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = tmp_path / "allowed"
    dataset = allowed / "pkg001"
    dataset.mkdir(parents=True)
    (dataset / "meta").mkdir()
    (dataset / "meta" / "info.json").write_text('{"codebase_version":"v3.0"}', encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()

    monkeypatch.setenv("LEROBOT_QC_DATASETS", str(allowed))
    monkeypatch.delenv("LEROBOT_QC_COLLECTIONS_REGISTRY", raising=False)

    assert validate_dataset_path(dataset) == dataset.resolve()

    with pytest.raises(ValueError, match="allowed roots"):
        validate_dataset_path(outside)


def test_validate_dataset_path_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEROBOT_QC_DATASETS", str(tmp_path))
    monkeypatch.delenv("LEROBOT_QC_COLLECTIONS_REGISTRY", raising=False)

    with pytest.raises(ValueError, match="not found"):
        validate_dataset_path(tmp_path / "missing")
