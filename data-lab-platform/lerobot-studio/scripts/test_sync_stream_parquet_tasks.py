"""Regression: episodes parquet must use per-session task labels, not live session."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sync_stream_parquet",
    Path(__file__).with_name("sync-stream-parquet.py"),
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _write_station(root: Path) -> None:
    (root / "live").mkdir(parents=True)
    (root / "meta").mkdir(parents=True)
    (root / "manifest").mkdir(parents=True)
    registry = {
        "activeSessionId": "sess_live99999999999999999999999999999999",
        "sessions": {
            "sess_old11111111111111111111111111111111": {
                "startedAt": "2026-09-07T01:34:22.910Z",
                "endedAt": None,
                "archiveDir": None,
            },
            "sess_live99999999999999999999999999999999": {
                "startedAt": "2026-09-10T08:24:39.942Z",
                "endedAt": None,
                "archiveDir": None,
            },
        },
    }
    (root / "live" / "session-registry.json").write_text(json.dumps(registry), encoding="utf-8")
    (root / "live" / "session.json").write_text(
        json.dumps(
            {
                "sessionId": "sess_live99999999999999999999999999999999",
                "startedAt": "2026-09-10T08:24:39.942Z",
                "task": "EGO-001 · live9999 · 09-10",
            }
        ),
        encoding="utf-8",
    )
    (root / "meta" / "info.json").write_text(
        json.dumps({"fps": 30, "total_frames": 125, "features": {}}),
        encoding="utf-8",
    )
    (root / "manifest" / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "station_id": "ego-001",
                "total_frames": 125,
                "episodes": [
                    {
                        "episode_index": 0,
                        "session_id": "sess_old11111111111111111111111111111111",
                        "frames": 125,
                        "from_index": 0,
                        "to_index": 124,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_episode_row_uses_session_registry_not_live_task() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "ego-001"
        root.mkdir()
        _write_station(root)
        live_task = _mod.resolve_live_task(root, json.loads((root / "live" / "session.json").read_text()))
        ep = _mod.episodes_from_unit_manifest(
            json.loads((root / "manifest" / "manifest.json").read_text())
        )[0]
        row = _mod._episode_row(
            ep,
            30.0,
            live_task,
            _mod.load_episode_meta_defaults(root),
            json.loads((root / "meta" / "info.json").read_text()),
            root=root,
            station_id="ego-001",
        )
        assert row["tasks"] == "EGO-001 · old11111 · 09-07 · 125f"
        assert "live9999" not in row["tasks"]
        assert "09-10" not in row["tasks"]


if __name__ == "__main__":
    test_episode_row_uses_session_registry_not_live_task()
    print("ok")
