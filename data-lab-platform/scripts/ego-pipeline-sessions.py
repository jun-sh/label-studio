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


def corpus_root_for(datalab_root: Path, slug: str) -> Path:
    return datalab_root / "data-storage" / "corpus" / slug


def samples_zip_for(datalab_root: Path, slug: str) -> Path:
    return datalab_root / "data-storage" / "samples" / f"{slug}.zip"


def pipeline_status_dir(datalab_root: Path, station: str, session_id: str) -> Path:
    return datalab_root / "data-storage" / "pipeline" / station / session_id / ".status"


def _unlink_marker(status_dir: Path, step: str) -> bool:
    removed = False
    done = status_dir / f"{step}.done"
    meta = status_dir / f"{step}.json"
    if done.is_file():
        done.unlink(missing_ok=True)
        removed = True
    if meta.is_file():
        meta.unlink(missing_ok=True)
        removed = True
    return removed


def reconcile_session_markers(
    datalab_root: Path,
    station: str,
    slug: str,
    *,
    apply: bool = True,
) -> list[str]:
    """Drop stale .status markers when their outputs are missing on disk."""
    actions: list[str] = []
    pipe_root = datalab_root / "data-storage" / "pipeline" / station
    if not pipe_root.is_dir():
        return actions

    corpus_info = corpus_root_for(datalab_root, slug) / "meta" / "info.json"
    samples_zip = samples_zip_for(datalab_root, slug)

    for sess_dir in sorted(pipe_root.glob("sess_*")):
        if not sess_dir.is_dir():
            continue
        status = pipeline_status_dir(datalab_root, station, sess_dir.name)
        if not status.is_dir():
            continue

        cleared: list[str] = []
        append_done = (status / "append.done").is_file()
        publish_done = (status / "publish.done").is_file()
        finalize_done = (status / "finalize.done").is_file()

        if append_done and not corpus_info.is_file():
            for step in ("append", "publish", "finalize"):
                if apply:
                    if _unlink_marker(status, step):
                        cleared.append(step)
                elif (status / f"{step}.done").is_file():
                    cleared.append(step)
        elif publish_done and not samples_zip.is_file():
            for step in ("publish", "finalize"):
                if apply:
                    if _unlink_marker(status, step):
                        cleared.append(step)
                elif (status / f"{step}.done").is_file():
                    cleared.append(step)
        elif finalize_done and not samples_zip.is_file():
            if apply:
                if _unlink_marker(status, "finalize"):
                    cleared.append("finalize")
            else:
                cleared.append("finalize")

        if cleared:
            actions.append(f"{sess_dir.name}: cleared stale markers {cleared}")

    return actions


def session_finalize_complete(
    datalab_root: Path,
    station: str,
    session_id: str,
    slug: str,
) -> bool:
    if not oak_finalize_marker(datalab_root, station, session_id).is_file():
        return False
    return samples_zip_for(datalab_root, slug).is_file()


DEFAULT_PIPELINE_BACKEND = "oak"


def _session_parquet_ready(stream_root: Path, session_id: str) -> bool:
    try:
        from ego_platform.lerobot.stream_ready import session_parquet_ready

        return session_parquet_ready(stream_root, session_id)
    except ImportError:
        return True


def awaiting_convert_sessions(
    stream_root: Path,
    pipe_root: Path,
    station: str,
    *,
    backend: str = DEFAULT_PIPELINE_BACKEND,
    datalab_root: Path | None = None,
    corpus_slug: str | None = None,
) -> list[str]:
    awaiting: list[str] = []
    slug = corpus_slug
    if slug is None and datalab_root is not None:
        slug = station_slug(pipe_root, station)
    for sid in list_stream_sessions(stream_root):
        if backend == "oak":
            if datalab_root is None:
                raise ValueError("datalab_root required for oak backend")
            if slug and session_finalize_complete(datalab_root, station, sid, slug):
                continue
            if slug is None and oak_finalize_marker(datalab_root, station, sid).is_file():
                continue
        elif publish_marker(pipe_root, station, sid).is_file():
            continue
        awaiting.append(sid)
    return awaiting


def pending_sessions(
    stream_root: Path,
    pipe_root: Path,
    station: str,
    *,
    backend: str = DEFAULT_PIPELINE_BACKEND,
    datalab_root: Path | None = None,
) -> list[str]:
    slug = station_slug(pipe_root, station) if datalab_root is not None else None
    return [
        sid
        for sid in awaiting_convert_sessions(
            stream_root,
            pipe_root,
            station,
            backend=backend,
            datalab_root=datalab_root,
            corpus_slug=slug,
        )
        if _session_parquet_ready(stream_root, sid)
    ]


def all_awaiting_parquet_ready(
    stream_root: Path,
    pipe_root: Path,
    station: str,
    *,
    backend: str = DEFAULT_PIPELINE_BACKEND,
    datalab_root: Path | None = None,
) -> bool:
    slug = station_slug(pipe_root, station) if datalab_root is not None else None
    awaiting = awaiting_convert_sessions(
        stream_root,
        pipe_root,
        station,
        backend=backend,
        datalab_root=datalab_root,
        corpus_slug=slug,
    )
    if not awaiting:
        return True
    return all(_session_parquet_ready(stream_root, sid) for sid in awaiting)


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
    parser.add_argument(
        "command",
        choices=[
            "pending",
            "awaiting",
            "all-parquet-ready",
            "has-data",
            "slug",
            "all-sessions",
            "doctor",
            "reconcile-markers",
        ],
    )
    parser.add_argument("station")
    parser.add_argument("--datalab-root", type=Path, default=None)
    parser.add_argument("--pipe-root", type=Path, default=None)
    parser.add_argument(
        "--backend",
        choices=["legacy", "oak"],
        default=None,
        help=f"pipeline backend (default: env EGO_PIPELINE_BACKEND or {DEFAULT_PIPELINE_BACKEND})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="for reconcile-markers: remove stale markers (default: dry-run for doctor)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    datalab = (args.datalab_root or script_dir.parent.parent).resolve()
    pipe = (args.pipe_root or datalab.parent / "ego-hand-pipeline").resolve()
    backend = args.backend or __import__("os").environ.get(
        "EGO_PIPELINE_BACKEND", DEFAULT_PIPELINE_BACKEND
    )
    stream = stream_root_for(datalab, args.station)
    slug = station_slug(pipe, args.station)

    if args.command == "doctor":
        actions = reconcile_session_markers(datalab, args.station, slug, apply=False)
        if actions:
            for line in actions:
                print(line)
            return 1
        print("OK: no stale pipeline markers")
        return 0
    if args.command == "reconcile-markers":
        actions = reconcile_session_markers(datalab, args.station, slug, apply=True)
        for line in actions:
            print(line)
        return 0

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
    if args.command == "awaiting":
        for sid in awaiting_convert_sessions(
            stream,
            pipe,
            args.station,
            backend=backend,
            datalab_root=datalab,
            corpus_slug=slug,
        ):
            print(sid)
        return 0
    if args.command == "all-parquet-ready":
        ok = all_awaiting_parquet_ready(
            stream, pipe, args.station, backend=backend, datalab_root=datalab
        )
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
