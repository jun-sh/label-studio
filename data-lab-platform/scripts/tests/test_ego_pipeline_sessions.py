"""Unit tests for ego-pipeline-sessions order manifest helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPT_DIR / "ego-pipeline-sessions.py"
spec = importlib.util.spec_from_file_location("ego_pipeline_sessions", MODULE_PATH)
assert spec and spec.loader
sessions = importlib.util.module_from_spec(spec)
sys.modules["ego_pipeline_sessions"] = sessions
spec.loader.exec_module(sessions)


def test_build_order_manifest_from_finalize_done(tmp_path: Path) -> None:
    station = "ego-001"
    sid = "sess_20260904_120000"
    finalize = (
        tmp_path
        / "data-storage"
        / "pipeline"
        / station
        / sid
        / ".status"
        / "finalize.done"
    )
    finalize.parent.mkdir(parents=True)
    finalize.write_text("ok", encoding="utf-8")

    manifest = sessions.build_order_manifest(tmp_path, station, order_id="ORD-test")
    assert manifest["order_id"] == "ORD-test"
    assert manifest["session_ids"] == [sid]

    out = tmp_path / "order.json"
    sessions.write_order_manifest(out, manifest)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["session_ids"] == [sid]
