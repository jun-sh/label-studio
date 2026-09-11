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

remap_unit_table_to_global = _mod.remap_unit_table_to_global


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


def test_republish_unit_global_indices(tmp_path: Path | None = None) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "ego-001"
        root.mkdir()
        (root / "manifest").mkdir()
        (root / "meta").mkdir()
        (root / "derived" / "sess_a").mkdir(parents=True)
        (root / "derived" / "sess_b").mkdir(parents=True)
        (root / "meta" / "info.json").write_text(
            json.dumps({"fps": 30, "features": _mod.SYSTEM_FEATURE_SPECS}, indent=2),
            encoding="utf-8",
        )
        (root / "manifest" / "manifest.json").write_text(
            json.dumps(
                {
                    "total_frames": 5,
                    "episodes": [
                        {
                            "episode_index": 0,
                            "session_id": "sess_a",
                            "frames": 2,
                            "from_index": 0,
                            "to_index": 1,
                        },
                        {
                            "episode_index": 1,
                            "session_id": "sess_b",
                            "frames": 3,
                            "from_index": 2,
                            "to_index": 4,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        def _unit_table(rows: int) -> pa.Table:
            return pa.table(
                {
                    "frame_index": pa.array(list(range(rows)), type=pa.int64()),
                    "episode_index": pa.array([0] * rows, type=pa.int64()),
                    "index": pa.array(list(range(rows)), type=pa.int64()),
                    "task_index": pa.array([0] * rows, type=pa.int64()),
                    "timestamp": pa.array([float(i) / 30 for i in range(rows)], type=pa.float32()),
                    "observation.state": pa.array([[0.0] * 6] * rows, type=pa.list_(pa.float32(), 6)),
                }
            )

        pq.write_table(_unit_table(2), root / "derived" / "sess_a" / "data.parquet")
        pq.write_table(_unit_table(3), root / "derived" / "sess_b" / "data.parquet")
        count = _mod.republish_unit_data_shards(root)
        assert count == 2
        t0 = pq.read_table(root / "data" / "chunk-000" / "file-000.parquet")
        t1 = pq.read_table(root / "data" / "chunk-000" / "file-001.parquet")
        assert set(t0["episode_index"].to_pylist()) == {0}
        assert set(t1["episode_index"].to_pylist()) == {1}
        assert t1["frame_index"].to_pylist() == [2, 3, 4]
        assert _mod.validate_unit_manifest_data_shards(root) == []


def test_remap_unit_table_episode_local_timestamp() -> None:
    import pyarrow as pa

    table = pa.table(
        {
            "frame_index": pa.array([0, 1, 2], type=pa.int64()),
            "timestamp": pa.array([0.0, 0.033, 0.066], type=pa.float32()),
            "observation.state": pa.array([[0.0] * 6] * 3, type=pa.list_(pa.float32(), 6)),
        }
    )
    out = remap_unit_table_to_global(table, episode_index=2, from_index=100, fps=30.0)
    assert out["frame_index"].to_pylist() == [0, 1, 2]
    assert out["index"].to_pylist() == [100, 101, 102]
    assert [round(x, 3) for x in out["timestamp"].to_pylist()] == [0.0, 0.033, 0.067]
