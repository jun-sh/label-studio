"""Integration tests for concurrent multi-dataset API isolation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import app as qc_app
from qc_store import QcStore


@pytest.fixture(autouse=True)
def clear_sessions() -> None:
    qc_app.sessions.clear()


def _register_dataset(tmp_path: Path, name: str, operator_id: str) -> str:
    root = tmp_path / name
    root.mkdir()
    state = MagicMock()
    state.dataset_root = root
    state.episodes_df = __import__("pandas").DataFrame({"episode_index": [0]})
    state.episode_row = MagicMock(return_value=MagicMock())
    state.resolve_language_instruction = MagicMock(return_value=(f"task-{name}", 0))
    state.fps = 10.0
    state.episode_length = MagicMock(return_value=100)
    state.video_keys = MagicMock(return_value=["cam"])
    state.video_key = "cam"
    state.build_summary = MagicMock(
        return_value={
            "root": str(root),
            "total_episodes": 1,
            "episodes": [{"episode_index": 0}],
            "fps": 10.0,
        }
    )
    store = QcStore.open(root, tmp_path / "sidecar", operator_id=operator_id)
    qc_app.sessions.open(state=state, store=store, local_path=str(root))
    return str(root)


def test_concurrent_packages_use_isolated_sessions(tmp_path: Path) -> None:
    path_a = _register_dataset(tmp_path, "681447", "reviewer-01")
    path_b = _register_dataset(tmp_path, "681495", "reviewer-02")
    session_a = qc_app.sessions.get_by_path(path_a)
    session_b = qc_app.sessions.get_by_path(path_b)
    assert session_a is not None
    assert session_b is not None

    qc_app.api_qc_review(
        qc_app.ReviewRequest(episode_index=0, status="approved"),
        session=session_a,
    )
    qc_app.api_qc_review(
        qc_app.ReviewRequest(episode_index=0, status="suspicious"),
        session=session_b,
    )

    info_a = qc_app.api_dataset_info(session=session_a)
    info_b = qc_app.api_dataset_info(session=session_b)
    payload_a = __import__("json").loads(info_a.body)
    payload_b = __import__("json").loads(info_b.body)

    assert payload_a["session_path"] == path_a
    assert payload_b["session_path"] == path_b
    assert payload_a["qc_state"]["0"]["review"]["status"] == "approved"
    assert payload_b["qc_state"]["0"]["review"]["status"] == "suspicious"


def test_require_session_rejects_missing_context() -> None:
    request = MagicMock()
    request.headers = {}
    with pytest.raises(HTTPException) as exc:
        qc_app.require_session(request=request, local_path=None)
    assert exc.value.status_code == 400


def test_require_session_rejects_unknown_path() -> None:
    request = MagicMock()
    request.headers = {"X-Dataset-Path": "/nonexistent/package"}
    with pytest.raises(HTTPException) as exc:
        qc_app.require_session(request=request, local_path=None)
    assert exc.value.status_code == 404
