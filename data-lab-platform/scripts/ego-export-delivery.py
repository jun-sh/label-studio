#!/usr/bin/env python3
"""EGO commercial delivery export — order-scoped per-episode slices."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

EXPORT_CONTRACT_VERSION = "1.1"
OUTCOME_ENUM = ("success", "fail", "partial")
ANNOTATION_STATUS_ENUM = ("none", "partial", "complete")
VALID_OUTCOMES = frozenset(OUTCOME_ENUM)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path, default: object = None) -> object:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_version(datalab_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(datalab_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        tag = (proc.stdout or "").strip()
        return tag or None
    except OSError:
        return None


def _slug_for_station(datalab_root: Path, station: str) -> str:
    sessions_py = datalab_root / "data-lab-platform" / "scripts" / "ego-pipeline-sessions.py"
    if sessions_py.is_file():
        proc = subprocess.run(
            ["python3", str(sessions_py), "slug", station, "--datalab-root", str(datalab_root)],
            capture_output=True,
            text=True,
            check=False,
        )
        slug = (proc.stdout or "").strip()
        if slug:
            return slug
    return "ego_001"


def _finalize_marker(datalab_root: Path, station: str, session_id: str) -> Path:
    return (
        datalab_root
        / "data-storage"
        / "pipeline"
        / station
        / session_id
        / ".status"
        / "finalize.done"
    )


def _ready_marker(stream_root: Path, session_id: str) -> Path:
    return stream_root / "state" / "sessions" / session_id / "session.READY"


def _episode_map(stream_root: Path) -> dict[str, dict]:
    manifest = _read_json(stream_root / "manifest" / "manifest.json", {})
    out: dict[str, dict] = {}
    if isinstance(manifest, dict):
        for ep in manifest.get("episodes") or []:
            if isinstance(ep, dict) and ep.get("session_id"):
                out[str(ep["session_id"])] = ep
    return out


def _canonical_dataset_key(dataset_root: Path) -> str:
    """Match lerobot-qc qc_store.canonical_dataset_key for cross-mount sidecar lookup."""
    parts = list(dataset_root.resolve().parts)
    start = 0
    for index, part in enumerate(parts):
        lowered = part.lower()
        if lowered in ("bookduo", "host-media") or part.startswith("BookDuo"):
            start = index + 1
    tail = parts[start:]
    if len(tail) >= 2:
        return f"{tail[-2]}/{tail[-1]}"
    if tail:
        return tail[-1]
    return dataset_root.name


def _find_qc_manifest(corpus_root: Path, qc_base: Path) -> dict | None:
    if not qc_base.is_dir():
        return None
    target_key = _canonical_dataset_key(corpus_root)
    for entry in qc_base.iterdir():
        manifest_path = entry / "qc_manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = _read_json(manifest_path, {})
        if not isinstance(manifest, dict):
            continue
        root = manifest.get("dataset_root")
        if not root:
            continue
        stored_key = _canonical_dataset_key(Path(str(root)))
        if stored_key == target_key:
            return manifest
    return None


def _qc_status(qc_manifest: dict | None, episode_index: int | None) -> str:
    if qc_manifest is None or episode_index is None:
        return "pending"
    reviews = qc_manifest.get("reviews") or {}
    review = reviews.get(str(episode_index)) or {}
    return str(review.get("status") or "pending")


def _load_annotations(corpus_root: Path) -> dict[str, Any]:
    raw = _read_json(corpus_root / "meta" / "lerobot_annotations.json", {})
    if not isinstance(raw, dict):
        return {}
    episodes = raw.get("episodes") or {}
    if not isinstance(episodes, dict):
        return {}
    return episodes


def _annotation_record(annotations: dict[str, Any], episode_index: int | None) -> dict[str, Any] | None:
    if episode_index is None:
        return None
    record = annotations.get(str(episode_index))
    return record if isinstance(record, dict) else None


def _annotation_status(subtasks: list[dict[str, Any]] | None, outcome: str | None) -> str:
    has_subtasks = bool(subtasks)
    has_outcome = outcome in VALID_OUTCOMES
    if not has_subtasks and not has_outcome:
        return "none"
    if has_subtasks and has_outcome:
        return "complete"
    return "partial"


def _idle_bookend_reasons(subtasks: list[dict[str, Any]] | None) -> list[str]:
    if not subtasks:
        return ["annotation_missing_subtasks"]
    ordered = sorted(subtasks, key=lambda seg: float(seg.get("start", 0)))
    reasons: list[str] = []
    if str(ordered[0].get("label") or "") != "idle":
        reasons.append("annotation_missing_leading_idle")
    if str(ordered[-1].get("label") or "") != "idle":
        reasons.append("annotation_missing_trailing_idle")
    return reasons


def _annotation_gate_reasons(ann: dict[str, Any] | None) -> list[str]:
    if ann is None:
        return ["annotation_missing"]
    subtasks = ann.get("subtasks") if isinstance(ann.get("subtasks"), list) else []
    outcome = ann.get("outcome")
    status = _annotation_status(subtasks, outcome if isinstance(outcome, str) else None)
    reasons: list[str] = []
    if status != "complete":
        reasons.append(f"annotation_{status}")
    if outcome not in VALID_OUTCOMES:
        reasons.append("annotation_outcome_missing")
    reasons.extend(_idle_bookend_reasons(subtasks))
    return reasons


def _slo_b_ok(stream_root: Path, datalab_root: Path, station: str, session_id: str) -> bool | None:
    ready_path = _ready_marker(stream_root, session_id)
    finalize_path = _finalize_marker(datalab_root, station, session_id)
    if not ready_path.is_file() or not finalize_path.is_file():
        return None
    ready = _read_json(ready_path, {})
    ready_at = ready.get("at") if isinstance(ready, dict) else None
    if not ready_at:
        return None
    try:
        ready_ms = datetime.fromisoformat(str(ready_at).replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None
    finalize_ms = finalize_path.stat().st_mtime * 1000
    slo_sec = int(os.environ.get("EGO_CONVERT_SLO_B_SEC", "600"))
    return (finalize_ms - ready_ms) <= slo_sec * 1000


def evaluate_delivery_gate(
    *,
    stream_root: Path,
    datalab_root: Path,
    station: str,
    session_id: str,
    episode: dict,
    qc_manifest: dict | None,
    annotations: dict[str, Any],
    require_qc: bool,
    require_annotation: bool = True,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not _ready_marker(stream_root, session_id).is_file():
        reasons.append("preview_not_ready")
    if not _finalize_marker(datalab_root, station, session_id).is_file():
        reasons.append("pose_not_ready")
    episode_index = episode.get("episode_index")
    qc_status = _qc_status(qc_manifest, episode_index if episode_index is not None else None)
    if qc_status in ("rejected", "suspicious"):
        reasons.append(f"qc_{qc_status}")
    if require_qc and qc_status != "approved":
        reasons.append(f"qc_{qc_status}")
    if require_annotation:
        reasons.extend(_annotation_gate_reasons(_annotation_record(annotations, episode_index)))
    slo = _slo_b_ok(stream_root, datalab_root, station, session_id)
    if slo is False:
        reasons.append("convert_slo_b_exceeded")
    return (len(reasons) == 0, reasons)


def load_order_manifest(path: Path) -> dict[str, Any]:
    raw = _read_json(path, None)
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid order manifest: {path}")
    order_id = str(raw.get("order_id") or "").strip()
    session_ids = raw.get("session_ids")
    if not order_id:
        raise ValueError("order manifest missing order_id")
    if not isinstance(session_ids, list) or not session_ids:
        raise ValueError("order manifest missing session_ids")
    cleaned = [str(sid).strip() for sid in session_ids if str(sid).strip()]
    if not cleaned:
        raise ValueError("order manifest session_ids is empty")
    return {
        "order_id": order_id,
        "customer_id": str(raw.get("customer_id") or "").strip() or None,
        "session_ids": cleaned,
    }


def _load_info(corpus_root: Path) -> dict[str, Any]:
    info_path = corpus_root / "meta" / "info.json"
    if not info_path.is_file():
        raise FileNotFoundError(f"Missing {info_path}")
    raw = _read_json(info_path, {})
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid info.json at {info_path}")
    return raw


def _load_episodes_df(corpus_root: Path) -> pd.DataFrame:
    episodes_root = corpus_root / "meta" / "episodes"
    files = sorted(episodes_root.rglob("*.parquet")) if episodes_root.is_dir() else []
    if not files:
        raise FileNotFoundError(f"No episodes parquet under {episodes_root}")
    df = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    if "episode_index" not in df.columns:
        raise ValueError("episodes parquet missing episode_index")
    return df.sort_values("episode_index").reset_index(drop=True)


def _video_keys(info: dict[str, Any]) -> list[str]:
    features = info.get("features") or {}
    return sorted(key for key, spec in features.items() if spec.get("dtype") == "video")


def _video_rel_path(info: dict[str, Any], video_key: str, chunk_index: int, file_index: int) -> str:
    features = info.get("features") or {}
    feature_info = (features.get(video_key) or {}).get("info") or {}
    rel_tpl = (
        feature_info.get("depth.video_path")
        or info.get("video_path")
        or "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
    )
    return rel_tpl.format(video_key=video_key, chunk_index=chunk_index, file_index=file_index)


def _resolve_video_path(corpus_root: Path, info: dict[str, Any], row: pd.Series, video_key: str) -> Path | None:
    chunk_col = f"videos/{video_key}/chunk_index"
    file_col = f"videos/{video_key}/file_index"
    if chunk_col not in row.index or file_col not in row.index:
        return None
    if pd.isna(row[chunk_col]) or pd.isna(row[file_col]):
        return None
    rel = _video_rel_path(info, video_key, int(row[chunk_col]), int(row[file_col]))
    path = corpus_root / rel
    if path.is_file():
        return path
    alt = path.with_suffix(".mkv" if path.suffix.lower() == ".mp4" else ".mp4")
    return alt if alt.is_file() else None


def _copy_file_record(src: Path, dest: Path, dest_root: Path) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)
    rel = dest.relative_to(dest_root).as_posix()
    return {"path": rel, "sha256": _sha256_file(dest), "bytes": dest.stat().st_size}


def _write_subtasks_parquet(dest_meta: Path, episode_index: int, subtasks: list[dict[str, Any]]) -> None:
    if not subtasks:
        return
    labels = sorted({str(seg.get("label")) for seg in subtasks if seg.get("label")})
    lookup = {label: idx for idx, label in enumerate(labels)}
    rows = []
    for seg in sorted(subtasks, key=lambda item: float(item.get("start", 0))):
        label = str(seg.get("label") or "")
        if not label:
            continue
        rows.append(
            {
                "episode_index": int(episode_index),
                "subtask": label,
                "subtask_index": lookup[label],
                "start": float(seg.get("start", 0)),
                "end": float(seg.get("end", 0)),
            }
        )
    if rows:
        pd.DataFrame(rows).to_parquet(dest_meta / "subtasks.parquet", engine="pyarrow", compression="snappy")


def _write_episodes_meta(dest_meta: Path, episode_index: int, ann: dict[str, Any] | None) -> None:
    subtasks = ann.get("subtasks") if ann and isinstance(ann.get("subtasks"), list) else []
    outcome = ann.get("outcome") if ann else None
    status = _annotation_status(subtasks, outcome if isinstance(outcome, str) else None)
    schema_ref = ann.get("schema_ref") if ann else None
    row = {
        "episode_index": int(episode_index),
        "outcome": outcome,
        "annotation_status": status,
        "schema_ref": schema_ref,
        "annotated": status in ("partial", "complete"),
    }
    pd.DataFrame([row]).to_parquet(dest_meta / "episodes_meta.parquet", engine="pyarrow", compression="snappy")


def export_episode_slice(
    *,
    corpus_root: Path,
    dest_root: Path,
    episode_index: int,
    ann: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    info = _load_info(corpus_root)
    episodes_df = _load_episodes_df(corpus_root)
    row = episodes_df[episodes_df["episode_index"] == episode_index]
    if row.empty:
        raise FileNotFoundError(f"Episode {episode_index} not found in corpus")
    episode_row = row.iloc[0]

    file_records: list[dict[str, Any]] = []

    dest_meta = dest_root / "meta"
    dest_meta.mkdir(parents=True, exist_ok=True)
    for name in ("info.json",):
        src = corpus_root / "meta" / name
        if src.is_file():
            dest = dest_meta / name
            file_records.append(_copy_file_record(src, dest, dest_root))

    tasks_src = corpus_root / "meta" / "tasks.parquet"
    if tasks_src.is_file():
        tasks_df = pd.read_parquet(tasks_src)
        if "task_index" in episode_row.index and pd.notna(episode_row["task_index"]):
            tasks_df = tasks_df[tasks_df["task_index"] == int(episode_row["task_index"])]
        dest = dest_meta / "tasks.parquet"
        tasks_df.to_parquet(dest, engine="pyarrow", compression="snappy")
        file_records.append(
            {"path": "meta/tasks.parquet", "sha256": _sha256_file(dest), "bytes": dest.stat().st_size}
        )

    episodes_dir = corpus_root / "meta" / "episodes"
    if episodes_dir.is_dir():
        dest_episodes = dest_meta / "episodes"
        dest_episodes.mkdir(parents=True, exist_ok=True)
        for src in sorted(episodes_dir.rglob("*.parquet")):
            df = pd.read_parquet(src)
            filtered = df[df["episode_index"] == episode_index]
            if filtered.empty:
                continue
            rel = src.relative_to(corpus_root / "meta" / "episodes")
            dest = dest_episodes / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            filtered.to_parquet(dest, engine="pyarrow", compression="snappy", index=False)
            file_records.append(
                {"path": ("meta/episodes/" + rel.as_posix()), "sha256": _sha256_file(dest), "bytes": dest.stat().st_size}
            )

    data_dir = corpus_root / "data"
    if data_dir.is_dir():
        for src in sorted(data_dir.rglob("*.parquet")):
            df = pd.read_parquet(src)
            if "episode_index" not in df.columns:
                continue
            filtered = df[df["episode_index"] == episode_index]
            if filtered.empty:
                continue
            rel = src.relative_to(corpus_root)
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            filtered.to_parquet(dest, engine="pyarrow", compression="snappy", index=False)
            file_records.append(
                {"path": rel.as_posix(), "sha256": _sha256_file(dest), "bytes": dest.stat().st_size}
            )

    imu_root = corpus_root / "sensor_raw" / "imu"
    if imu_root.is_dir():
        for src in sorted(imu_root.rglob("*.parquet")):
            df = pd.read_parquet(src)
            if "episode_index" in df.columns:
                filtered = df[df["episode_index"] == episode_index]
                if filtered.empty:
                    continue
            else:
                filtered = df
            rel = src.relative_to(corpus_root)
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            filtered.to_parquet(dest, engine="pyarrow", compression="snappy", index=False)
            file_records.append(
                {"path": rel.as_posix(), "sha256": _sha256_file(dest), "bytes": dest.stat().st_size}
            )

    for video_key in _video_keys(info):
        src_video = _resolve_video_path(corpus_root, info, episode_row, video_key)
        if src_video is None:
            continue
        rel = src_video.relative_to(corpus_root).as_posix()
        dest = dest_root / rel
        file_records.append(_copy_file_record(src_video, dest, dest_root))

    _write_episodes_meta(dest_meta, episode_index, ann)
    episodes_meta = dest_meta / "episodes_meta.parquet"
    file_records.append(
        {
            "path": "meta/episodes_meta.parquet",
            "sha256": _sha256_file(episodes_meta),
            "bytes": episodes_meta.stat().st_size,
        }
    )

    subtasks = ann.get("subtasks") if ann and isinstance(ann.get("subtasks"), list) else []
    _write_subtasks_parquet(dest_meta, episode_index, subtasks)
    subtasks_path = dest_meta / "subtasks.parquet"
    if subtasks_path.is_file():
        file_records.append(
            {
                "path": "meta/subtasks.parquet",
                "sha256": _sha256_file(subtasks_path),
                "bytes": subtasks_path.stat().st_size,
            }
        )

    return file_records


def build_export_manifest(
    *,
    station: str,
    session_id: str,
    episode: dict,
    order_id: str,
    customer_id: str | None,
    files: list[dict[str, Any]],
    gates: dict[str, Any],
    datalab_root: Path,
    corpus_root: Path,
    ann: dict[str, Any] | None,
) -> dict[str, Any]:
    provenance = episode.get("provenance") if isinstance(episode.get("provenance"), dict) else {}
    corpus_hash = None
    ann_path = corpus_root / "meta" / "lerobot_annotations.json"
    if ann_path.is_file():
        corpus_hash = _sha256_file(ann_path)
    subtasks = ann.get("subtasks") if ann and isinstance(ann.get("subtasks"), list) else []
    return {
        "export_contract_version": EXPORT_CONTRACT_VERSION,
        "export_contract": "ego_export_contract@1.1",
        "station_id": station,
        "order_id": order_id,
        "customer_id": customer_id,
        "source_session_id": session_id,
        "episode_index": episode.get("episode_index"),
        "pose_ready": True,
        "video_codec": episode.get("video_codec") or provenance.get("video_codec") or "jpeg",
        "pipeline_version": episode.get("pipeline_version") or provenance.get("pipeline_version"),
        "exported_at": _utc_now(),
        "provenance": {
            "corpus_root": str(corpus_root.resolve()),
            "lerobot_annotations_sha256": corpus_hash,
            "code_version": _code_version(datalab_root),
        },
        "gates": gates,
        "annotation_summary": {
            "schema_ref": (ann or {}).get("schema_ref"),
            "outcome": (ann or {}).get("outcome"),
            "annotation_status": _annotation_status(
                subtasks,
                (ann or {}).get("outcome") if isinstance((ann or {}).get("outcome"), str) else None,
            ),
            "l2_segment_count": len(subtasks),
        },
        "files": files,
        "enums": {
            "outcome": list(OUTCOME_ENUM),
            "annotation_status": list(ANNOTATION_STATUS_ENUM),
        },
    }


def run_gate(
    datalab_root: Path,
    station: str,
    order: dict[str, Any],
    *,
    require_qc: bool,
    require_annotation: bool = True,
) -> tuple[int, list[dict[str, Any]]]:
    stream_root = datalab_root / "data-storage" / "stream" / station
    slug = _slug_for_station(datalab_root, station)
    corpus_root = datalab_root / "data-storage" / "corpus" / slug
    qc_base = datalab_root / "data-storage" / "lerobot-qc"
    qc_manifest = _find_qc_manifest(corpus_root, qc_base)
    episode_map = _episode_map(stream_root)
    annotations = _load_annotations(corpus_root)

    results: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for session_id in order["session_ids"]:
        episode = episode_map.get(session_id, {"session_id": session_id})
        ok, reasons = evaluate_delivery_gate(
            stream_root=stream_root,
            datalab_root=datalab_root,
            station=station,
            session_id=session_id,
            episode=episode,
            qc_manifest=qc_manifest,
            annotations=annotations,
            require_qc=require_qc,
            require_annotation=require_annotation,
        )
        entry = {"session_id": session_id, "ok": ok, "reasons": reasons}
        results.append(entry)
        if not ok:
            blocked.append(entry)

    if blocked:
        print(
            json.dumps(
                {"ok": False, "order_id": order["order_id"], "blocked": blocked},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1, results
    print(
        json.dumps(
            {
                "ok": True,
                "order_id": order["order_id"],
                "station": station,
                "sessions": len(order["session_ids"]),
            }
        )
    )
    return 0, results


def run_export(
    datalab_root: Path,
    station: str,
    order: dict[str, Any],
    *,
    require_qc: bool,
    require_annotation: bool = True,
) -> int:
    code, gate_results = run_gate(
        datalab_root,
        station,
        order,
        require_qc=require_qc,
        require_annotation=require_annotation,
    )
    if code != 0:
        return code

    stream_root = datalab_root / "data-storage" / "stream" / station
    slug = _slug_for_station(datalab_root, station)
    corpus_root = datalab_root / "data-storage" / "corpus" / slug
    if not corpus_root.is_dir():
        print(f"FAIL: missing corpus {corpus_root}", file=sys.stderr)
        return 1

    qc_base = datalab_root / "data-storage" / "lerobot-qc"
    qc_manifest = _find_qc_manifest(corpus_root, qc_base)
    annotations = _load_annotations(corpus_root)
    episode_map = _episode_map(stream_root)

    order_root = datalab_root / "data-storage" / "ego-delivery" / "orders" / order["order_id"]
    order_root.mkdir(parents=True, exist_ok=True)
    acceptance_episodes: list[dict[str, Any]] = []

    for session_id, gate_row in zip(order["session_ids"], gate_results):
        episode = episode_map.get(session_id, {"session_id": session_id})
        episode_index = episode.get("episode_index")
        if episode_index is None:
            print(f"FAIL: missing episode_index for {session_id}", file=sys.stderr)
            return 1
        ann = _annotation_record(annotations, int(episode_index))
        dest = order_root / "episodes" / session_id
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

        files = export_episode_slice(
            corpus_root=corpus_root,
            dest_root=dest,
            episode_index=int(episode_index),
            ann=ann,
        )
        gates = {
            "preview_ready": True,
            "pose_ready": True,
            "qc_status": _qc_status(qc_manifest, episode_index),
            "annotation_status": _annotation_status(
                ann.get("subtasks") if ann else None,
                ann.get("outcome") if ann and isinstance(ann.get("outcome"), str) else None,
            ),
            "outcome": ann.get("outcome") if ann else None,
            "gate_reasons": gate_row.get("reasons") or [],
        }
        manifest = build_export_manifest(
            station=station,
            session_id=session_id,
            episode=episode,
            order_id=order["order_id"],
            customer_id=order.get("customer_id"),
            files=files,
            gates=gates,
            datalab_root=datalab_root,
            corpus_root=corpus_root,
            ann=ann,
        )
        manifest_path = dest / "meta" / "export_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        acceptance_episodes.append(
            {
                "source_session_id": session_id,
                "episode_index": episode_index,
                "gates_pass": True,
                "gate_reasons": [],
                "package_path": f"episodes/{session_id}/",
                "export_manifest_sha256": _sha256_file(manifest_path),
            }
        )

    passed = len(acceptance_episodes)
    acceptance = {
        "order_id": order["order_id"],
        "customer_id": order.get("customer_id"),
        "station_id": station,
        "contract": "ego_export_contract@1.1",
        "generated_at": _utc_now(),
        "episode_count": passed,
        "episodes": acceptance_episodes,
        "summary": {
            "passed": passed,
            "failed": 0,
            "manifest_triple_green_rate": 1.0 if passed else 0.0,
        },
    }
    acceptance_path = order_root / "acceptance_report.json"
    acceptance_path.write_text(json.dumps(acceptance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = {
        "ok": True,
        "order_id": order["order_id"],
        "station": station,
        "order_root": str(order_root),
        "acceptance_report": str(acceptance_path),
        "exported_sessions": order["session_ids"],
        "contract": "ego_export_contract@1.1",
    }
    print(json.dumps(summary, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="EGO commercial delivery export (order-scoped)")
    parser.add_argument("station", help="station id, e.g. ego-001")
    parser.add_argument(
        "--order-manifest",
        type=Path,
        required=True,
        help="JSON with order_id and session_ids whitelist",
    )
    parser.add_argument("--datalab-root", type=Path, default=None)
    parser.add_argument("--gate-only", action="store_true", help="run delivery gate only")
    parser.add_argument(
        "--no-require-qc",
        action="store_true",
        help="skip QC approved requirement (rollback/debug)",
    )
    parser.add_argument(
        "--no-require-annotation",
        action="store_true",
        help="skip annotation completeness gate (E2E/ops validation)",
    )
    args = parser.parse_args()
    script_dir = Path(__file__).resolve().parent
    datalab_root = args.datalab_root or script_dir.parent.parent
    require_qc = not args.no_require_qc and os.environ.get("EGO_EXPORT_REQUIRE_QC", "1").strip() not in (
        "0",
        "false",
        "no",
    )
    require_annotation = not args.no_require_annotation and os.environ.get(
        "EGO_EXPORT_REQUIRE_ANNOTATION", "1"
    ).strip() not in ("0", "false", "no")
    try:
        order = load_order_manifest(args.order_manifest.expanduser().resolve())
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if args.gate_only:
        code, _ = run_gate(
            datalab_root,
            args.station,
            order,
            require_qc=require_qc,
            require_annotation=require_annotation,
        )
        return code
    return run_export(
        datalab_root,
        args.station,
        order,
        require_qc=require_qc,
        require_annotation=require_annotation,
    )


if __name__ == "__main__":
    raise SystemExit(main())
