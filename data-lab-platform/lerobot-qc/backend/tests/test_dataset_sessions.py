"""Tests for per-dataset session registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dataset_sessions import DATASET_PATH_HEADER, DatasetSessionRegistry
from qc_store import QcStore


def _mock_state(root: Path) -> MagicMock:
    state = MagicMock()
    state.dataset_root = root
    return state


def test_sessions_isolate_different_packages(tmp_path: Path) -> None:
    registry = DatasetSessionRegistry(max_sessions=8, ttl_seconds=3600)
    root_a = tmp_path / "681447"
    root_b = tmp_path / "681495"
    root_a.mkdir()
    root_b.mkdir()

    store_a = QcStore.open(root_a, tmp_path / "sidecar", operator_id="reviewer-a")
    store_b = QcStore.open(root_b, tmp_path / "sidecar", operator_id="reviewer-b")
    session_a = registry.open(state=_mock_state(root_a), store=store_a, local_path=str(root_a))
    session_b = registry.open(state=_mock_state(root_b), store=store_b, local_path=str(root_b))

    assert session_a.dataset_key != session_b.dataset_key
    assert registry.get_by_path(str(root_a)) is session_a
    assert registry.get_by_path(str(root_b)) is session_b
    assert registry.get_by_path(str(root_a)) is not session_b


def test_sessions_resolve_alias_paths(tmp_path: Path) -> None:
    registry = DatasetSessionRegistry()
    root = tmp_path / "pkg"
    root.mkdir()
    store = QcStore.open(root, tmp_path / "sidecar", operator_id="tester")
    registry.open(state=_mock_state(root), store=store, local_path=str(root))

    resolved = registry.get_by_path(str(root.resolve()))
    assert resolved is not None
    assert resolved.local_path == str(root.resolve())


def test_sessions_evict_oldest_when_full(tmp_path: Path) -> None:
    registry = DatasetSessionRegistry(max_sessions=2, ttl_seconds=3600)
    roots = []
    for name in ("a", "b", "c"):
        root = tmp_path / name
        root.mkdir()
        roots.append(root)
        store = QcStore.open(root, tmp_path / "sidecar", operator_id="tester")
        registry.open(state=_mock_state(root), store=store, local_path=str(root))

    assert registry.get_by_path(str(roots[0])) is None
    assert registry.get_by_path(str(roots[1])) is not None
    assert registry.get_by_path(str(roots[2])) is not None


def test_dataset_path_header_constant() -> None:
    assert DATASET_PATH_HEADER == "X-Dataset-Path"
