#!/usr/bin/env python3
"""Session discovery for ego-run-pipeline (stream ↔ postprocess state)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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


def _session_marker_exists(stream_root: Path, session_id: str, marker: str) -> bool:
    return (stream_root / "state" / "sessions" / session_id / marker).is_file()


def _session_failed(stream_root: Path, session_id: str) -> bool:
    return _session_marker_exists(stream_root, session_id, "session.FAILED")


def _add_session_id(sessions: set[str], session_id: object) -> None:
    sid = str(session_id or "").strip()
    if sid.startswith("sess_"):
        sessions.add(sid)


def list_stream_sessions(stream_root: Path) -> list[str]:
    sessions: set[str] = set()

    live = _read_json(stream_root / "live" / "session.json", {})
    if isinstance(live, dict):
        _add_session_id(sessions, live.get("sessionId"))

    registry = _read_json(stream_root / "live" / "session-registry.json", {})
    if isinstance(registry, dict):
        for sid in (registry.get("sessions") or {}):
            _add_session_id(sessions, sid)

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

    # Unit layout + MCAP pilot: session markers / derived units / manifest episodes.
    sessions_dir = stream_root / "state" / "sessions"
    if sessions_dir.is_dir():
        for sess_dir in sessions_dir.iterdir():
            if not sess_dir.is_dir() or not sess_dir.name.startswith("sess_"):
                continue
            if (sess_dir / "session.READY").is_file():
                sessions.add(sess_dir.name)

    derived = stream_root / "derived"
    if derived.is_dir():
        for sess_dir in derived.iterdir():
            if sess_dir.is_dir() and sess_dir.name.startswith("sess_"):
                if (sess_dir / "unit.json").is_file():
                    sessions.add(sess_dir.name)

    manifest = _read_json(stream_root / "manifest" / "manifest.json", {})
    if isinstance(manifest, dict):
        for ep in manifest.get("episodes") or []:
            if isinstance(ep, dict):
                _add_session_id(sessions, ep.get("session_id"))

    return sorted(sid for sid in sessions if not _session_failed(stream_root, sid))


def session_source_format(stream_root: Path, session_id: str) -> str:
    """Return dominant ingest sourceFormat for a session (mcap | tarzst | mixed | unknown)."""
    unit_path = stream_root / "derived" / session_id / "unit.json"
    if unit_path.is_file():
        unit = _read_json(unit_path, {})
        if isinstance(unit, dict):
            derive = unit.get("derive")
            if isinstance(derive, dict):
                fmt = str(derive.get("source_format") or "").strip()
                if fmt:
                    return fmt
            for seg in unit.get("source_segments") or []:
                if isinstance(seg, dict):
                    fmt = str(seg.get("source_format") or "").strip()
                    if fmt:
                        return fmt

    seg_dir = stream_root / "state" / "segments" / session_id
    formats: set[str] = set()
    if seg_dir.is_dir():
        for state_file in seg_dir.glob("*.json"):
            state = _read_json(state_file, {})
            if isinstance(state, dict):
                fmt = str(state.get("sourceFormat") or "").strip()
                if fmt:
                    formats.add(fmt)
    if formats == {"mcap"}:
        return "mcap"
    if formats == {"tarzst"}:
        return "tarzst"
    if len(formats) > 1:
        return "mixed"
    if (stream_root / "raw" / "segments" / session_id).is_dir():
        raw_dir = stream_root / "raw" / "segments" / session_id
        has_mcap = any(raw_dir.glob("*.mcap.zst"))
        has_tar = any(raw_dir.glob("*.tar.zst"))
        if has_mcap and not has_tar:
            return "mcap"
        if has_tar and not has_mcap:
            return "tarzst"
        if has_mcap and has_tar:
            return "mixed"
    return "unknown"


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


def order_manifest_path(datalab_root: Path, station: str) -> Path:
    return datalab_root / "data-storage" / "ego-delivery" / station / "orders" / "active-order.json"


def exportable_sessions(datalab_root: Path, station: str) -> list[str]:
    """Sessions with pose_ready finalize.done (commercial export whitelist)."""
    pipe_root = datalab_root / "data-storage" / "pipeline" / station
    if not pipe_root.is_dir():
        return []
    ready: list[str] = []
    for sess_dir in sorted(pipe_root.glob("sess_*")):
        if not sess_dir.is_dir():
            continue
        if oak_finalize_marker(datalab_root, station, sess_dir.name).is_file():
            ready.append(sess_dir.name)
    return ready


def build_order_manifest(
    datalab_root: Path,
    station: str,
    *,
    order_id: str | None = None,
    customer_id: str | None = None,
) -> dict[str, object]:
    session_ids = exportable_sessions(datalab_root, station)
    if not session_ids:
        raise ValueError(f"no exportable sessions (missing finalize.done) for {station}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    oid = (order_id or "").strip() or f"ORD-{station}-{stamp}"
    return {
        "order_id": oid,
        "customer_id": (customer_id or "").strip() or "internal",
        "session_ids": session_ids,
    }


def write_order_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
    ready_marker = stream_root / "state" / "sessions" / session_id / "session.READY"
    if ready_marker.is_file():
        return True
    try:
        from ego_platform.lerobot.stream_ready import session_parquet_ready

        return session_parquet_ready(stream_root, session_id)
    except ImportError:
        return False


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
        sess_dir = stream_root / "state" / "sessions" / sid
        if _session_quarantined(sess_dir):
            continue
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


def derive_pending_sessions(stream_root: Path) -> list[str]:
    """Sessions with DONE_UPLOAD but not yet READY (upload order)."""
    sessions_dir = stream_root / "state" / "sessions"
    if not sessions_dir.is_dir():
        return []

    pending: list[tuple[str, str]] = []
    for sess_dir in sorted(sessions_dir.glob("sess_*")):
        if not sess_dir.is_dir():
            continue
        sid = sess_dir.name
        if not (sess_dir / "session.DONE_UPLOAD").is_file():
            continue
        if (sess_dir / "session.READY").is_file():
            continue
        if (sess_dir / "session.FAILED").is_file():
            continue
        if _session_quarantined(sess_dir):
            continue
        marker = sess_dir / "session.DONE_UPLOAD"
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
            at = str(data.get("at") or data.get("uploadedAt") or sid)
        except (OSError, json.JSONDecodeError):
            at = sid
        pending.append((at, sid))

    return [sid for _, sid in sorted(pending)]


DEFAULT_REQUEUE_FAILED_CODES = frozenset(
    {
        "RECONCILE_EXCEEDED",
        "PARQUET_INDEX_GAP",
        "h264_trim_no_idr",
        "MUX_FRAME_MISMATCH",
        "MUX_DECODE_FAILED",
        "BROWSER_NOT_PLAYABLE",
    }
)

QUARANTINE_MARKER = "session.QUARANTINED"
REQUEUE_STATE_FILE = "session.requeue.json"
# Capture-stall raw (no IDR in the H.264 elementary stream) can never derive; retrying
# it forever blocks ego-process, so quarantine after this many attempts.
MAX_REQUEUE_ATTEMPTS = 1
# Failures whose raw data is unusable no matter how often derive re-runs.
UNRECOVERABLE_FAILED_PATTERNS = ("h264_trim_no_idr",)


def _session_quarantined(sess_dir: Path) -> bool:
    return (sess_dir / QUARANTINE_MARKER).is_file()


def _requeue_attempts(sess_dir: Path) -> int:
    data = _read_json(sess_dir / REQUEUE_STATE_FILE, {})
    if not isinstance(data, dict):
        return 0
    try:
        return int(data.get("attempts") or 0)
    except (TypeError, ValueError):
        return 0


def _record_requeue_attempt(sess_dir: Path, attempts: int, reason: str) -> None:
    payload = {
        "attempts": attempts,
        "lastReason": reason,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        (sess_dir / REQUEUE_STATE_FILE).write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def quarantine_session(sess_dir: Path, reason: str, *, code: str = "") -> None:
    payload = {
        "sessionId": sess_dir.name,
        "marker": QUARANTINE_MARKER,
        "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": {
            "code": code or "UNRECOVERABLE_RAW",
            "message": reason,
            "category": "derive",
        },
    }
    try:
        (sess_dir / QUARANTINE_MARKER).write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def quarantine_failed_sessions(
    stream_root: Path,
    *,
    session_ids: list[str] | None = None,
    apply: bool = False,
) -> list[str]:
    """Mark unrecoverable FAILED sessions so requeue/wait-derive skip them."""
    sessions_dir = stream_root / "state" / "sessions"
    if not sessions_dir.is_dir():
        return []

    wanted = {sid.strip() for sid in (session_ids or []) if sid.strip()}
    marked: list[str] = []
    for sess_dir in sorted(sessions_dir.glob("sess_*")):
        if not sess_dir.is_dir():
            continue
        sid = sess_dir.name
        if wanted and sid not in wanted:
            continue
        failed_path = sess_dir / "session.FAILED"
        if not failed_path.is_file():
            continue
        if (sess_dir / "session.READY").is_file():
            continue
        if _session_quarantined(sess_dir):
            continue
        if (
            not wanted
            and _requeue_attempts(sess_dir) < MAX_REQUEUE_ATTEMPTS
            and not _failed_session_unrecoverable(failed_path)
        ):
            continue
        marked.append(sid)
        if apply:
            quarantine_session(
                sess_dir,
                _failed_session_reason_message(failed_path) or "unrecoverable raw",
                code=_failed_session_reason_code(failed_path),
            )
    return marked


def _failed_session_reason_code(failed_path: Path) -> str:
    data = _read_json(failed_path, {})
    if not isinstance(data, dict):
        return ""
    reason = data.get("reason")
    if isinstance(reason, dict):
        return str(reason.get("code") or "").strip()
    return str(data.get("code") or "").strip()


def _failed_session_reason_message(failed_path: Path) -> str:
    data = _read_json(failed_path, {})
    if not isinstance(data, dict):
        return ""
    reason = data.get("reason")
    if isinstance(reason, dict):
        return str(reason.get("message") or "").strip()
    return str(data.get("message") or "").strip()


def _failed_session_unrecoverable(failed_path: Path) -> bool:
    blob = f"{_failed_session_code_or_message(failed_path)}"
    return any(pat in blob for pat in UNRECOVERABLE_FAILED_PATTERNS)


def _failed_session_code_or_message(failed_path: Path) -> str:
    return f"{_failed_session_reason_code(failed_path)} {_failed_session_reason_message(failed_path)}"


def _failed_session_requeue_match(failed_path: Path, allowed: set[str]) -> bool:
    code = _failed_session_reason_code(failed_path)
    if code in allowed:
        return True
    message = _failed_session_reason_message(failed_path)
    if "h264_trim_no_idr" in message and (
        "h264_trim_no_idr" in allowed or code == "GATE_INTERNAL_ERROR"
    ):
        return True
    return False


def requeue_failed_sessions(
    stream_root: Path,
    *,
    codes: set[str] | None = None,
    apply: bool = False,
) -> list[str]:
    """Clear session.FAILED so derive can retry (post-fix re-derive for mux failures)."""
    sessions_dir = stream_root / "state" / "sessions"
    if not sessions_dir.is_dir():
        return []

    allowed = set(codes) if codes else set(DEFAULT_REQUEUE_FAILED_CODES)
    requeued: list[str] = []
    for sess_dir in sorted(sessions_dir.glob("sess_*")):
        if not sess_dir.is_dir():
            continue
        sid = sess_dir.name
        failed_path = sess_dir / "session.FAILED"
        if not failed_path.is_file():
            continue
        if (sess_dir / "session.READY").is_file():
            continue
        if not (sess_dir / "session.DONE_UPLOAD").is_file():
            continue
        if _session_quarantined(sess_dir):
            continue
        if not _failed_session_requeue_match(failed_path, allowed):
            continue
        attempts = _requeue_attempts(sess_dir)
        reason = _failed_session_reason_message(failed_path)
        if attempts >= MAX_REQUEUE_ATTEMPTS or _failed_session_unrecoverable(failed_path):
            if apply:
                quarantine_session(
                    sess_dir,
                    reason or "requeue attempts exhausted",
                    code=_failed_session_reason_code(failed_path),
                )
            continue
        requeued.append(sid)
        if apply:
            _record_requeue_attempt(sess_dir, attempts + 1, reason)
            try:
                failed_path.unlink()
            except OSError:
                pass
    return requeued


def _station_yaml_block(pipe_root: Path, station: str) -> str | None:
    cfg = pipe_root / "configs" / "stations.yaml"
    if not cfg.is_file():
        return None
    import re

    text = cfg.read_text(encoding="utf-8")
    block = re.search(
        rf"(?ms)^  {re.escape(station)}:\s*\n(.*?)(?=^  \w|\Z)",
        text,
    )
    return block.group(1) if block else None


def _station_yaml_field(pipe_root: Path, station: str, field: str) -> str | None:
    block = _station_yaml_block(pipe_root, station)
    if not block:
        return None
    import re

    m = re.search(rf"{re.escape(field)}:\s*(\S+)", block)
    return m.group(1) if m else None


def station_slug(pipe_root: Path, station: str) -> str:
    """Corpus / samples filesystem slug (dataset_slug), not viewer manifest id."""
    defaults = {
        "ego-001": "ego_001",
        "ego-lab-01": "ego_lab_01_hand_pose",
        "ego-field-02": "ego_field_02_hand_pose",
        "ego-mcap-pilot": "ego_mcap_pilot",
        "ego-mcap-track2": "ego_mcap_track2",
    }
    slug = _station_yaml_field(pipe_root, station, "dataset_slug")
    if slug:
        return slug
    manifest_id = _station_yaml_field(pipe_root, station, "manifest_id")
    if manifest_id:
        return manifest_id
    return defaults.get(station, station.replace("-", "_"))


def station_manifest_id(pipe_root: Path, station: str) -> str:
    """Viewer /data/:id manifest entry id."""
    defaults = {
        "ego-001": "ego-001",
        "ego-lab-01": "ego_lab_01_hand_pose",
        "ego-field-02": "ego_field_02_hand_pose",
        "ego-mcap-pilot": "ego_mcap_pilot",
        "ego-mcap-track2": "ego_mcap_track2",
    }
    manifest_id = _station_yaml_field(pipe_root, station, "manifest_id")
    if manifest_id:
        return manifest_id
    return defaults.get(station, station.replace("-", "_"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "pending",
            "awaiting",
            "derive-pending",
            "requeue-failed",
            "quarantine-failed",
            "all-parquet-ready",
            "has-data",
            "slug",
            "manifest-id",
            "all-sessions",
            "doctor",
            "reconcile-markers",
            "source-format",
            "ready-sessions",
            "exportable-sessions",
            "build-order-manifest",
        ],
    )
    parser.add_argument("station")
    parser.add_argument("session_id", nargs="?", default="", help="for source-format")
    parser.add_argument("--datalab-root", type=Path, default=None)
    parser.add_argument("--pipe-root", type=Path, default=None)
    parser.add_argument(
        "--backend",
        choices=["legacy", "oak"],
        default=None,
        help=f"pipeline backend (default: env EGO_PIPELINE_BACKEND or {DEFAULT_PIPELINE_BACKEND})",
    )
    parser.add_argument(
        "--code",
        action="append",
        default=[],
        help="for requeue-failed: limit to derive failure code (repeatable)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="for reconcile-markers: remove stale markers (default: dry-run for doctor); for requeue-failed: clear session.FAILED",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="for build-order-manifest: write JSON path (default active-order.json)",
    )
    parser.add_argument("--order-id", default="", help="for build-order-manifest")
    parser.add_argument("--customer-id", default="", help="for build-order-manifest")
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
    if args.command == "manifest-id":
        print(station_manifest_id(pipe, args.station))
        return 0
    if args.command == "all-sessions":
        for sid in list_stream_sessions(stream):
            print(sid)
        return 0
    if args.command == "ready-sessions":
        sessions_dir = stream / "state" / "sessions"
        ready: list[str] = []
        if sessions_dir.is_dir():
            for sess_dir in sorted(sessions_dir.glob("sess_*")):
                if (sess_dir / "session.READY").is_file():
                    ready.append(sess_dir.name)
        for sid in ready:
            print(sid)
        return 0
    if args.command == "exportable-sessions":
        for sid in exportable_sessions(datalab, args.station):
            print(sid)
        return 0
    if args.command == "build-order-manifest":
        try:
            manifest = build_order_manifest(
                datalab,
                args.station,
                order_id=args.order_id or None,
                customer_id=args.customer_id or None,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        out = args.output or order_manifest_path(datalab, args.station)
        write_order_manifest(out, manifest)
        print(json.dumps({"path": str(out), **manifest}, ensure_ascii=False))
        return 0
    if args.command == "source-format":
        if not str(args.session_id or "").strip():
            print("missing session_id (usage: source-format <station> <session_id>)", file=sys.stderr)
            return 2
        print(session_source_format(stream, str(args.session_id).strip()))
        return 0
    if args.command == "pending":
        for sid in pending_sessions(stream, pipe, args.station, backend=backend, datalab_root=datalab):
            print(sid)
        return 0
    if args.command == "derive-pending":
        for sid in derive_pending_sessions(stream):
            print(sid)
        return 0
    if args.command == "requeue-failed":
        codes = set(args.code) if args.code else None
        requeued = requeue_failed_sessions(stream, codes=codes, apply=bool(args.apply))
        for sid in requeued:
            print(sid)
        return 0
    if args.command == "quarantine-failed":
        explicit = [s for s in [str(args.session_id or "").strip()] if s]
        marked = quarantine_failed_sessions(
            stream, session_ids=explicit or None, apply=bool(args.apply)
        )
        for sid in marked:
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
