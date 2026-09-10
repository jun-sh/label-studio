"""Unit tests for ego-export-delivery gate helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPT_DIR / "ego-export-delivery.py"
spec = importlib.util.spec_from_file_location("ego_export_delivery", MODULE_PATH)
assert spec and spec.loader
export = importlib.util.module_from_spec(spec)
sys.modules["ego_export_delivery"] = export
spec.loader.exec_module(export)


def test_annotation_gate_requires_complete_outcome_and_idle() -> None:
    ann = {
        "outcome": "success",
        "subtasks": [
            {"start": 0.0, "end": 1.0, "label": "idle"},
            {"start": 1.0, "end": 2.0, "label": "reach"},
            {"start": 2.0, "end": 3.0, "label": "idle"},
        ],
    }
    assert export._annotation_gate_reasons(ann) == []

    incomplete = {
        "outcome": "success",
        "subtasks": [{"start": 0.0, "end": 1.0, "label": "reach"}],
    }
    reasons = export._annotation_gate_reasons(incomplete)
    assert "annotation_missing_leading_idle" in reasons
    assert "annotation_missing_trailing_idle" in reasons


def test_load_order_manifest(tmp_path: Path) -> None:
    path = tmp_path / "order.json"
    path.write_text(
        json.dumps(
            {
                "order_id": "ORD-1",
                "customer_id": "C1",
                "session_ids": ["sess_a", "sess_b"],
            }
        ),
        encoding="utf-8",
    )
    order = export.load_order_manifest(path)
    assert order["order_id"] == "ORD-1"
    assert order["session_ids"] == ["sess_a", "sess_b"]


def test_evaluate_delivery_gate_blocks_without_qc_and_annotation(tmp_path: Path) -> None:
    stream_root = tmp_path / "stream"
    datalab_root = tmp_path
    station = "ego-001"
    session_id = "sess_test"
    sess_state = stream_root / "state" / "sessions" / session_id
    sess_state.mkdir(parents=True)
    (sess_state / "session.READY").write_text("{}", encoding="utf-8")
    finalize = (
        datalab_root
        / "data-storage"
        / "pipeline"
        / station
        / session_id
        / ".status"
        / "finalize.done"
    )
    finalize.parent.mkdir(parents=True)
    finalize.write_text("ok", encoding="utf-8")

    ok, reasons = export.evaluate_delivery_gate(
        stream_root=stream_root,
        datalab_root=datalab_root,
        station=station,
        session_id=session_id,
        episode={"episode_index": 0},
        qc_manifest=None,
        annotations={},
        require_qc=True,
    )
    assert not ok
    assert "qc_pending" in reasons
    assert "annotation_missing" in reasons


def test_evaluate_delivery_gate_skips_annotation_when_disabled(tmp_path: Path) -> None:
    stream_root = tmp_path / "stream"
    datalab_root = tmp_path
    station = "ego-001"
    session_id = "sess_test"
    sess_state = stream_root / "state" / "sessions" / session_id
    sess_state.mkdir(parents=True)
    (sess_state / "session.READY").write_text("{}", encoding="utf-8")
    finalize = (
        datalab_root
        / "data-storage"
        / "pipeline"
        / station
        / session_id
        / ".status"
        / "finalize.done"
    )
    finalize.parent.mkdir(parents=True)
    finalize.write_text("ok", encoding="utf-8")

    ok, reasons = export.evaluate_delivery_gate(
        stream_root=stream_root,
        datalab_root=datalab_root,
        station=station,
        session_id=session_id,
        episode={"episode_index": 0},
        qc_manifest=None,
        annotations={},
        require_qc=False,
        require_annotation=False,
    )
    assert "annotation_missing" not in reasons


def test_find_qc_manifest_matches_canonical_dataset_key(tmp_path: Path) -> None:
    corpus_host = tmp_path / "data-storage" / "corpus" / "egodome"
    corpus_host.mkdir(parents=True)
    qc_base = tmp_path / "sidecar"
    sidecar_dir = qc_base / "egodome_abc"
    sidecar_dir.mkdir(parents=True)
    manifest = {
        "version": 1,
        "dataset_root": "/data/corpus/egodome",
        "reviews": {"0": {"status": "approved"}},
    }
    (sidecar_dir / "qc_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    found = export._find_qc_manifest(corpus_host, qc_base)
    assert found is not None
    assert export._qc_status(found, 0) == "approved"
