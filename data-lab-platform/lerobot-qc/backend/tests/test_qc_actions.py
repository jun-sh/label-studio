"""Tests for QC review and instruction API validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import app as qc_app
from qc_store import QcStore


@pytest.fixture()
def loaded_qc(tmp_path: Path) -> tuple[MagicMock, QcStore]:
    dataset_root = tmp_path / "source"
    dataset_root.mkdir()
    sidecar = tmp_path / "sidecar"

    state = MagicMock()
    state.episodes_df = __import__("pandas").DataFrame({"episode_index": [0, 1, 2]})
    state.episode_row = MagicMock(return_value=MagicMock())
    state.resolve_language_instruction = MagicMock(return_value=("original task", 0))

    store = QcStore.open(dataset_root, sidecar, operator_id="tester")
    qc_app.state = state
    qc_app.store = store
    return state, store


def test_reject_requires_reason(loaded_qc: tuple[MagicMock, QcStore]) -> None:
    with pytest.raises(HTTPException) as exc:
        qc_app.api_qc_review(qc_app.ReviewRequest(episode_index=0, status="rejected"))
    assert exc.value.status_code == 400


def test_instruction_rejects_empty_text(loaded_qc: tuple[MagicMock, QcStore]) -> None:
    with pytest.raises(HTTPException) as exc:
        qc_app.api_qc_instruction(qc_app.InstructionRequest(episode_index=0, instruction="   "))
    assert exc.value.status_code == 400


def test_instruction_rejects_removed_episode(loaded_qc: tuple[MagicMock, QcStore]) -> None:
    _, store = loaded_qc
    store.set_review(0, "rejected", reason="bad_quality")
    with pytest.raises(HTTPException) as exc:
        qc_app.api_qc_instruction(qc_app.InstructionRequest(episode_index=0, instruction="new text"))
    assert exc.value.status_code == 400


def test_instruction_keeps_suspicious_status(loaded_qc: tuple[MagicMock, QcStore]) -> None:
    _, store = loaded_qc
    store.set_review(1, "suspicious", note="flagged")
    response = qc_app.api_qc_instruction(qc_app.InstructionRequest(episode_index=1, instruction="corrected task"))
    payload = __import__("json").loads(response.body)
    assert payload["instruction"] == "corrected task"
    assert store.get_review(1)["status"] == "suspicious"


def test_instruction_auto_approves_pending(loaded_qc: tuple[MagicMock, QcStore]) -> None:
    _, store = loaded_qc
    response = qc_app.api_qc_instruction(qc_app.InstructionRequest(episode_index=2, instruction="corrected task"))
    payload = __import__("json").loads(response.body)
    assert payload["review"]["status"] == "approved"
