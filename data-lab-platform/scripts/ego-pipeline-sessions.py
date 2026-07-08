#!/usr/bin/env python3
"""Session discovery for ego-run-pipeline (stream ↔ postprocess state)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _read_json(path: Path, default: object) -> object:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def stream_root_for(datalab_root: Path, station: str) -> Path:
    return datalab_root / "data-storage" / "stream" / station


def list_stream_sessions(stream_root: Path) -> list[str]:
    sessions: set[str] = set()

    live = _read_json(stream_root / "live" / "session.json", {})
    if isinstance(live, dict):
        sid = str(live.get("sessionId") or "").strip()
        if sid:
            sessions.add(sid)

    registry = _read_json(stream_root / "live" / "session-registry.json", {})
    if isinstance(registry, dict):
        for sid in (registry.get("sessions") or {}):
            if str(sid).strip():
                sessions.add(str(sid).strip())

    archive = stream_root / "archive"
    if archive.is_dir():
        for entry in archive.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name.startswith("sess_"):
                parts = name.split("_")
                if len(parts) >= 2:
                    sessions.add(f"{parts[0]}_{parts[1]}")

    return sorted(sessions)


def stream_has_ingested_data(stream_root: Path) -> bool:
    info = _read_json(stream_root / "meta" / "info.json", {})
    if isinstance(info, dict) and int(info.get("total_frames") or 0) > 0:
        return True
    archive = stream_root / "archive"
    if archive.is_dir():
        for entry in archive.iterdir():
            if entry.is_dir() and (entry / "meta" / "info.json").is_file():
                sub = _read_json(entry / "meta" / "info.json", {})
                if isinstance(sub, dict) and int(sub.get("total_frames") or 0) > 0:
                    return True
    return False


def publish_marker(pipe_root: Path, station: str, session_id: str) -> Path:
    return pipe_root / "outputs" / station / session_id / ".status" / "publish.done"


def oak_finalize_marker(datalab_root: Path, station: str, session_id: str) -> Path:
    return (
        datalab_root
        / "data-storage"
        / "pipeline"
        / station
        / session_id
        / ".status"
        / "finalize.done"
    )


def pending_sessions(
    stream_root: Path,
    pipe_root: Path,
    station: str,
    *,
    backend: str = "legacy",
    datalab_root: Path | None = None,
) -> list[str]:
    pending: list[str] = []
    for sid in list_stream_sessions(stream_root):
        if backend == "oak":
            if datalab_root is None:
                raise ValueError("datalab_root required for oak backend")
            if oak_finalize_marker(datalab_root, station, sid).is_file():
                continue
        elif publish_marker(pipe_root, station, sid).is_file():
            continue
        pending.append(sid)
    return pending


def station_slug(pipe_root: Path, station: str) -> str:
    defaults = {
        "ego-lan-214": "ego_214_hand_pose",
        "ego-lab-01": "ego_lab_01_hand_pose",
        "ego-field-02": "ego_field_02_hand_pose",
    }
    cfg = pipe_root / "configs" / "stations.yaml"
    if cfg.is_file():
        import re

        text = cfg.read_text(encoding="utf-8")
        block = re.search(
            rf"(?ms)^  {re.escape(station)}:\s*\n(.*?)(?=^  \w|\Z)",
            text,
        )
        if block:
            m = re.search(r"manifest_id:\s*(\S+)", block.group(1))
            if m:
                return m.group(1)
    return defaults.get(station, station.replace("-", "_"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["pending", "has-data", "slug", "all-sessions"])
    parser.add_argument("station")
    parser.add_argument("--datalab-root", type=Path, default=None)
    parser.add_argument("--pipe-root", type=Path, default=None)
    parser.add_argument(
        "--backend",
        choices=["legacy", "oak"],
        default=None,
        help="pipeline backend (default: env EGO_PIPELINE_BACKEND or legacy)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    datalab = (args.datalab_root or script_dir.parent.parent).resolve()
    pipe = (args.pipe_root or datalab.parent / "ego-hand-pipeline").resolve()
    backend = args.backend or __import__("os").environ.get("EGO_PIPELINE_BACKEND", "legacy")
    stream = stream_root_for(datalab, args.station)

    if args.command == "has-data":
        return 0 if stream_has_ingested_data(stream) else 1
    if args.command == "slug":
        print(station_slug(pipe, args.station))
        return 0
    if args.command == "all-sessions":
        for sid in list_stream_sessions(stream):
            print(sid)
        return 0
    if args.command == "pending":
        for sid in pending_sessions(stream, pipe, args.station, backend=backend, datalab_root=datalab):
            print(sid)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
