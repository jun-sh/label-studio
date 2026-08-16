"""Physical LeRobot v3.0 dataset rebuild — output to a new directory only."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from dataset_manager import DatasetState, _load_tasks_map
from qc_metrics import read_episode_frames
from qc_store import QcStore
from stats_service import recompute_dataset_stats
from video_delivery import format_video_progress, selective_process_videos
from video_service import expected_video_duration, trim_video_for_delivery

CHUNKS_SIZE_DEFAULT = 1000
REBUILD_MODE = os.environ.get("LEROBOT_QC_REBUILD_MODE", "minimal").strip().lower()
RebuildMode = Literal["minimal", "legacy"]

logger = logging.getLogger(__name__)

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_running_by_dataset: dict[str, str] = {}
_running_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_rebuild_mode(mode: str | None = None) -> RebuildMode:
    selected = (mode or REBUILD_MODE or "minimal").strip().lower()
    if selected not in ("minimal", "legacy"):
        raise ValueError(f"Unsupported rebuild mode: {selected!r}")
    return selected  # type: ignore[return-value]


def find_rebuild_job_in_sidecar(job_id: str, sidecar_root: Path) -> dict[str, Any] | None:
    """Locate a rebuild job record without requiring the dataset session to be loaded."""
    if not sidecar_root.is_dir():
        return None
    for manifest_path in sidecar_root.glob("*/qc_manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for job in manifest.get("rebuild_jobs") or []:
            if job.get("job_id") == job_id:
                return dict(job)
    return None


def get_job(job_id: str, store: QcStore | None = None) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            return dict(job)
    if store is not None:
        persisted = store.get_rebuild_job(job_id)
        if persisted:
            _set_job(job_id, **_job_updates(persisted))
            return dict(persisted)
    return None


def resolve_rebuild_job(job_id: str, store: QcStore | None = None, sidecar_root: Path | None = None) -> dict[str, Any] | None:
    job = get_job(job_id, store=store)
    if job:
        return job
    if sidecar_root is not None:
        job = find_rebuild_job_in_sidecar(job_id, sidecar_root)
        if job:
            _set_job(job_id, **_job_updates(job))
            return dict(job)
    return None


def get_active_rebuild_job(store: QcStore) -> dict[str, Any] | None:
    active = store.active_rebuild_job()
    if not active:
        return None
    job_id = str(active.get("job_id") or "")
    if not job_id:
        return None
    return get_job(job_id, store=store) or dict(active)


def hydrate_jobs_from_store(store: QcStore) -> None:
    reconcile_stale_jobs(store)
    for job in store.manifest.get("rebuild_jobs") or []:
        job_id = job.get("job_id")
        if job_id:
            _set_job(str(job_id), **_job_updates(job))


def reconcile_stale_jobs(store: QcStore) -> None:
    """Mark orphaned queued/running jobs failed after a service restart."""
    for job in store.manifest.get("rebuild_jobs") or []:
        status = job.get("status")
        if status not in ("queued", "running"):
            continue
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        with _jobs_lock:
            if job_id in _jobs:
                continue
        failed = {
            "status": "failed",
            "finished_at": _utc_now(),
            "error": "Service restarted while rebuild was in progress",
        }
        store.update_rebuild_job(job_id, failed)
        _set_job(job_id, **failed)


def _dataset_key(dataset_root: Path) -> str:
    return str(dataset_root.resolve())


def _register_running_dataset(dataset_root: Path, job_id: str) -> None:
    key = _dataset_key(dataset_root)
    with _running_lock:
        active_job = _running_by_dataset.get(key)
        if active_job and active_job != job_id:
            raise ValueError(f"Rebuild already in progress for dataset: {active_job}")
        _running_by_dataset[key] = job_id


def _unregister_running_dataset(dataset_root: Path, job_id: str) -> None:
    key = _dataset_key(dataset_root)
    with _running_lock:
        if _running_by_dataset.get(key) == job_id:
            del _running_by_dataset[key]


def _cleanup_incomplete_output(output_root: Path) -> None:
    if not output_root.exists():
        return
    info_path = output_root / "meta" / "info.json"
    if info_path.is_file():
        return
    logger.warning("Removing incomplete rebuild output: %s", output_root)
    shutil.rmtree(output_root, ignore_errors=True)


def allocate_delivery_path(delivery_root: Path, task_name: str, batch_id: str) -> tuple[str, Path]:
    """Pick a unique delivery directory, suffixing batch_id when the base name exists."""
    suffix = 0
    while True:
        batch = batch_id if suffix == 0 else f"{batch_id}_{suffix}"
        output_root = delivery_root / f"{task_name}_v{batch}"
        if not output_root.exists():
            return batch, output_root
        suffix += 1


def _sync_job(job_id: str, store: QcStore, **updates: Any) -> None:
    _set_job(job_id, **updates)
    store.update_rebuild_job(job_id, updates)


def _set_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        job = _jobs.setdefault(job_id, {"job_id": job_id})
        job.update(updates)


def _job_updates(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "job_id"}


def _normalize_episode_tasks(value: Any) -> list[str] | None:
    """Normalize episode tasks to list[str] for parquet (avoid str/ndarray mix)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else None
    if isinstance(value, np.ndarray):
        items = [str(item).strip() for item in value.tolist()]
        items = [item for item in items if item]
        return items or None
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
        items = [item for item in items if item]
        return items or None
    text = str(value).strip()
    return [text] if text else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _atomic_write_parquet(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp)
    tmp.replace(path)


def _build_episode_mapping(
    all_indices: list[int],
    removed: set[int],
) -> tuple[list[int], dict[int, int]]:
    kept = [idx for idx in all_indices if idx not in removed]
    return kept, {old: new for new, old in enumerate(kept)}


def _copy_dataset_tree(src_root: Path, dst_root: Path, *, skip_videos: bool = False) -> None:
    if skip_videos:
        def _ignore_videos(_dir: str, names: list[str]) -> list[str]:
            return ["videos"] if "videos" in names else []

        shutil.copytree(src_root, dst_root, ignore=_ignore_videos, dirs_exist_ok=False)
        return
    shutil.copytree(src_root, dst_root, dirs_exist_ok=False)


def _update_splits(info: dict[str, Any], kept_count: int) -> None:
    splits = dict(info.get("splits") or {})
    for key, value in list(splits.items()):
        if isinstance(value, str) and ":" in value:
            start, _ = value.split(":", 1)
            splits[key] = f"{start}:{kept_count}"
    info["splits"] = splits


def _patch_episode_tasks(row: pd.Series, store: QcStore, original_index: int) -> pd.Series:
    override = store.get_instruction_override(original_index)
    if not override:
        return row
    patched = row.copy()
    patched["tasks"] = _normalize_episode_tasks(override)
    return patched


def _filter_episodes_parquet(
    episodes_df: pd.DataFrame,
    *,
    kept: list[int],
    old_to_new: dict[int, int],
    store: QcStore,
    video_metadata: dict[int, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Preserve original columns; patch episode_index, dataset bounds, tasks, and video refs."""
    rows: list[pd.Series] = []
    global_offset = 0
    for original_index in kept:
        src = episodes_df[episodes_df["episode_index"] == original_index].iloc[0]
        src = _patch_episode_tasks(src, store, original_index)
        length = int(src["length"]) if "length" in src.index and pd.notna(src["length"]) else 0
        patched = src.copy()
        new_index = old_to_new[original_index]
        patched["episode_index"] = new_index
        patched["dataset_from_index"] = global_offset
        patched["dataset_to_index"] = global_offset + length
        if video_metadata:
            for key, value in (video_metadata.get(new_index) or {}).items():
                patched[key] = value
        global_offset += length
        rows.append(patched)

    out = pd.DataFrame(rows)
    column_order = [col for col in episodes_df.columns if col in out.columns]
    extra_cols = [col for col in out.columns if col not in column_order]
    return out[column_order + extra_cols]


def _coerce_dataframe_to_schema(df: pd.DataFrame, schema: pa.Schema) -> pd.DataFrame:
    coerced = df.copy()
    for field in schema:
        if field.name not in coerced.columns:
            continue
        if pa.types.is_float32(field.type):
            coerced[field.name] = coerced[field.name].astype(np.float32)
        elif pa.types.is_int64(field.type):
            coerced[field.name] = coerced[field.name].astype(np.int64)
        elif pa.types.is_int32(field.type):
            coerced[field.name] = coerced[field.name].astype(np.int32)
    return coerced


def _filter_data_parquet(
    src_path: Path,
    dst_path: Path,
    *,
    removed: set[int],
    old_to_new: dict[int, int],
) -> int:
    table = pq.read_table(src_path)
    schema = table.schema
    df = table.to_pandas()
    if removed:
        df = df[~df["episode_index"].isin(removed)].copy()
    if old_to_new:
        df["episode_index"] = df["episode_index"].map(old_to_new)
    sort_cols = [col for col in ("episode_index", "frame_index") if col in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, kind="stable")
    if "index" in df.columns:
        df["index"] = np.arange(len(df), dtype=df["index"].dtype)
    df = _coerce_dataframe_to_schema(df, schema)
    _atomic_write_parquet(pa.Table.from_pandas(df, schema=schema, preserve_index=False), dst_path)
    return len(df)


def _write_delivery_report(
    store: QcStore,
    *,
    batch_id: str,
    report_lines: list[str],
) -> Path:
    report_dir = store.sidecar_root / "delivery_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{batch_id}.txt"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return report_path


def _delivery_report_lines(
    *,
    state: DatasetState,
    output_root: Path,
    batch_id: str,
    all_indices: list[int],
    kept: list[int],
    removed: set[int],
    store: QcStore,
    video_stats: dict[str, int] | None = None,
) -> list[str]:
    lines = [
        "LeRobot QC Delivery Report",
        f"Generated: {_utc_now()}",
        f"Source dataset: {state.dataset_root}",
        f"Output dataset: {output_root}",
        f"Batch ID: {batch_id}",
        f"Rebuild mode: minimal (selective re-encode)",
        f"Original episodes: {len(all_indices)}",
        f"Removed episodes: {len(removed)}",
        f"Delivered episodes: {len(kept)}",
        f"Instruction corrections: {len(store.manifest.get('instruction_overrides') or {})}",
    ]
    if video_stats:
        lines.append(
            "Video files: "
            f"{video_stats.get('copied', 0)} copied, "
            f"{video_stats.get('reencoded', 0)} re-encoded, "
            f"{video_stats.get('skipped', 0)} skipped"
        )
    return lines


def _try_finalize(output_root: Path) -> tuple[bool, str | None]:
    try:
        from lerobot.datasets.lerobot_dataset import LeRobotDataset

        ds = LeRobotDataset(repo_id=str(output_root), root=output_root)
        if hasattr(ds, "finalize"):
            ds.finalize()
            return True, None
        return False, "LeRobotDataset.finalize is unavailable"
    except Exception as exc:
        logger.warning("LeRobot finalize skipped for %s: %s", output_root, exc)
        return False, str(exc)
    return False, "LeRobot finalize did not run"


def _apply_instruction_corrections(
    tasks_map: dict[int, str],
    state: DatasetState,
    store: QcStore,
    kept_original_indices: list[int],
) -> dict[int, str]:
    updated = dict(tasks_map)
    overrides = store.manifest.get("instruction_overrides") or {}
    for ep_idx in kept_original_indices:
        override = overrides.get(str(ep_idx))
        if not override:
            continue
        row = state.episode_row(ep_idx)
        _, task_index = state.resolve_language_instruction(row)
        if task_index is not None:
            updated[task_index] = str(override)
    return updated


def _rebuild_tasks_parquet(output_root: Path, tasks_map: dict[int, str]) -> None:
    if not tasks_map:
        return
    rows = [{"task_index": idx, "task": text} for idx, text in sorted(tasks_map.items())]
    df = pd.DataFrame(rows)
    table = pa.Table.from_pandas(df, preserve_index=False)
    _atomic_write_parquet(table, output_root / "meta" / "tasks.parquet")


def _video_rel_path(state: DatasetState, video_key: str, chunk_index: int, file_index: int) -> str:
    features = state.info.get("features") or {}
    feature_info = (features.get(video_key) or {}).get("info") or {}
    rel_tpl = (
        feature_info.get("depth.video_path")
        or state.info.get("video_path")
        or "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
    )
    return rel_tpl.format(
        video_key=video_key,
        chunk_index=chunk_index,
        file_index=file_index,
    )


def _copy_episode_videos(
    state: DatasetState,
    *,
    src_episode_index: int,
    dst_episode_index: int,
    output_root: Path,
    chunks_size: int,
    frame_count: int,
    fps: float,
) -> dict[str, Any]:
    video_meta: dict[str, Any] = {}
    duration = expected_video_duration(frame_count, fps)
    for video_key in state.video_keys():
        src_path = state.video_path(src_episode_index, video_key=video_key)
        offsets = state.video_offsets(video_key, src_episode_index)
        chunk_index = dst_episode_index // chunks_size
        file_index = dst_episode_index % chunks_size
        rel_path = _video_rel_path(state, video_key, chunk_index, file_index)
        dst_path = output_root / rel_path
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        start = offsets["video_start_time"]
        end = offsets["video_end_time"]
        ok = trim_video_for_delivery(
            src_path,
            dst_path,
            start,
            end,
            frame_count,
            fps,
        )
        if not ok:
            raise RuntimeError(
                f"Failed to trim delivery video for episode {src_episode_index} "
                f"({video_key}) -> {dst_path}"
            )

        video_meta[f"videos/{video_key}/chunk_index"] = chunk_index
        video_meta[f"videos/{video_key}/file_index"] = file_index
        video_meta[f"videos/{video_key}/from_timestamp"] = 0.0
        video_meta[f"videos/{video_key}/to_timestamp"] = duration
    return video_meta


def _rebuild_minimal_delivery_dataset(
    *,
    state: DatasetState,
    store: QcStore,
    output_root: Path,
    batch_id: str,
    job_id: str,
) -> dict[str, Any]:
    removed = store.removed_episode_indices()
    all_indices = [int(x) for x in state.episodes_df["episode_index"].tolist()]
    kept, old_to_new = _build_episode_mapping(all_indices, removed)

    if output_root.exists():
        raise ValueError(f"Output directory already exists: {output_root}")

    _sync_job(job_id, store, progress="copying dataset", status="running")
    _copy_dataset_tree(state.dataset_root, output_root, skip_videos=True)

    total_frames = 0
    data_files = sorted((state.dataset_root / "data").rglob("*.parquet"))
    for index, src_parquet in enumerate(data_files, start=1):
        rel = src_parquet.relative_to(state.dataset_root / "data")
        _sync_job(
            job_id,
            store,
            progress=f"patching data {index}/{len(data_files)}",
            progress_current=index,
            progress_total=len(data_files),
            status="running",
        )
        total_frames += _filter_data_parquet(
            src_parquet,
            output_root / "data" / rel,
            removed=removed,
            old_to_new=old_to_new,
        )

    def _report_video_progress(
        current: int,
        total: int,
        action: str,
        video_key: str,
        chunk_index: int,
        file_index: int,
    ) -> None:
        _sync_job(
            job_id,
            store,
            progress=format_video_progress(action, video_key, chunk_index, file_index),
            progress_current=current,
            progress_total=total,
            status="running",
        )

    video_metadata, video_stats = selective_process_videos(
        info=state.info,
        episodes_df=state.episodes_df,
        video_keys=state.video_keys(),
        src_root=state.dataset_root,
        dst_root=output_root,
        kept_episode_indices=kept,
        old_to_new=old_to_new,
        fps=state.fps,
        on_progress=_report_video_progress,
    )

    episode_files = sorted((state.dataset_root / "meta" / "episodes").rglob("*.parquet"))
    for index, src_parquet in enumerate(episode_files, start=1):
        rel = src_parquet.relative_to(state.dataset_root / "meta" / "episodes")
        _sync_job(
            job_id,
            store,
            progress=f"patching episodes {index}/{len(episode_files)}",
            progress_current=index,
            progress_total=len(episode_files),
            status="running",
        )
        orig_df = pd.read_parquet(src_parquet)
        patched = _filter_episodes_parquet(
            orig_df,
            kept=kept,
            old_to_new=old_to_new,
            store=store,
            video_metadata=video_metadata,
        )
        schema = pq.read_schema(src_parquet)
        _atomic_write_parquet(
            pa.Table.from_pandas(patched, schema=schema, preserve_index=False),
            output_root / "meta" / "episodes" / rel,
        )

    info = json.loads((output_root / "meta" / "info.json").read_text(encoding="utf-8"))
    info["total_episodes"] = len(kept)
    info["total_frames"] = total_frames
    info["codebase_version"] = "v3.0"
    _update_splits(info, len(kept))
    _write_json(output_root / "meta" / "info.json", info)
    _sync_job(job_id, store, progress="recomputing stats", status="running")
    stats_recomputed = recompute_dataset_stats(output_root, info)
    if not stats_recomputed:
        logger.warning("Failed to fully recompute stats.json for %s", output_root)

    report_path = _write_delivery_report(
        store,
        batch_id=batch_id,
        report_lines=_delivery_report_lines(
            state=state,
            output_root=output_root,
            batch_id=batch_id,
            all_indices=all_indices,
            kept=kept,
            removed=removed,
            store=store,
            video_stats=video_stats,
        ),
    )

    return {
        "output_root": str(output_root),
        "kept_episodes": len(kept),
        "removed_episodes": len(removed),
        "finalized": False,
        "rebuild_mode": "minimal",
        "report_path": str(report_path),
        "videos_copied": video_stats.get("copied", 0),
        "videos_reencoded": video_stats.get("reencoded", 0),
        "stats_recomputed": stats_recomputed,
    }


def _rebuild_legacy_delivery_dataset(
    *,
    state: DatasetState,
    store: QcStore,
    output_root: Path,
    batch_id: str,
    job_id: str,
) -> dict[str, Any]:
    removed = store.removed_episode_indices()
    all_indices = [int(x) for x in state.episodes_df["episode_index"].tolist()]
    kept = [idx for idx in all_indices if idx not in removed]

    if output_root.exists():
        raise ValueError(f"Output directory already exists: {output_root}")
    output_root.mkdir(parents=True, exist_ok=False)

    chunks_size = int(state.info.get("chunks_size") or CHUNKS_SIZE_DEFAULT)
    fps = state.fps
    tasks_map = _apply_instruction_corrections(_load_tasks_map(state.dataset_root), state, store, kept)

    episode_rows: list[dict[str, Any]] = []
    frame_tables: list[pd.DataFrame] = []
    global_frame_offset = 0

    for new_index, original_index in enumerate(kept):
        progress_current = new_index + 1
        progress_total = len(kept)
        _sync_job(
            job_id,
            store,
            progress=f"episode {progress_current}/{progress_total}",
            progress_current=progress_current,
            progress_total=progress_total,
            status="running",
        )
        row = state.episode_row(original_index)
        ep_df = read_episode_frames(state, original_index)
        if ep_df.empty:
            length = state.episode_length(row)
        else:
            length = len(ep_df)

        from_index = global_frame_offset
        to_index = global_frame_offset + length
        global_frame_offset = to_index

        if not ep_df.empty:
            ep_df = ep_df.copy()
            ep_df["episode_index"] = new_index
            if "frame_index" in ep_df.columns:
                ep_df["frame_index"] = np.arange(length, dtype=np.int64)
            if "index" in ep_df.columns:
                ep_df["index"] = np.arange(from_index, to_index, dtype=np.int64)
            if "timestamp" in ep_df.columns:
                ep_df["timestamp"] = np.arange(length, dtype=np.float64) / fps
            frame_tables.append(ep_df)

        ep_record: dict[str, Any] = {
            "episode_index": new_index,
            "length": length,
            "dataset_from_index": from_index,
            "dataset_to_index": to_index,
        }
        if "task_index" in row.index and pd.notna(row["task_index"]):
            ep_record["task_index"] = int(row["task_index"])
        override = store.get_instruction_override(original_index)
        if override:
            ep_record["tasks"] = _normalize_episode_tasks(override)
        elif "tasks" in row.index and pd.notna(row.get("tasks")):
            ep_record["tasks"] = _normalize_episode_tasks(row.get("tasks"))

        ep_record.update(
            _copy_episode_videos(
                state,
                src_episode_index=original_index,
                dst_episode_index=new_index,
                output_root=output_root,
                chunks_size=chunks_size,
                frame_count=length,
                fps=fps,
            )
        )
        episode_rows.append(ep_record)

    if frame_tables:
        frames_df = pd.concat(frame_tables, ignore_index=True)
        data_chunk = output_root / "data" / "chunk-000" / "file-000.parquet"
        _atomic_write_parquet(pa.Table.from_pandas(frames_df, preserve_index=False), data_chunk)

    episodes_df = pd.DataFrame(episode_rows)
    episodes_path = output_root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    _atomic_write_parquet(pa.Table.from_pandas(episodes_df, preserve_index=False), episodes_path)

    info = dict(state.info)
    info["total_episodes"] = len(kept)
    info["total_frames"] = int(global_frame_offset)
    info["chunks_size"] = chunks_size
    info["codebase_version"] = "v3.0"
    _update_splits(info, len(kept))
    _write_json(output_root / "meta" / "info.json", info)
    _rebuild_tasks_parquet(output_root, tasks_map)

    stats = {
        "episode_count": len(kept),
        "frame_count": int(global_frame_offset),
        "removed_count": len(removed),
        "rebuilt_at": _utc_now(),
        "source_root": str(state.dataset_root),
    }
    _write_json(output_root / "meta" / "stats.json", stats)

    removed_txt = output_root / "removed_episodes.txt"
    removed_txt.write_text("\n".join(str(x) for x in sorted(removed)) + ("\n" if removed else ""), encoding="utf-8")
    if store.corrections_path.is_file():
        shutil.copy2(store.corrections_path, output_root / "annotation_corrections.csv")
    if store.removed_path.is_file():
        shutil.copy2(store.removed_path, output_root / "removed_episodes.json")

    report_lines = [
        "LeRobot QC Delivery Report",
        f"Generated: {_utc_now()}",
        f"Source dataset: {state.dataset_root}",
        f"Output dataset: {output_root}",
        f"Batch ID: {batch_id}",
        f"Rebuild mode: legacy",
        f"Original episodes: {len(all_indices)}",
        f"Removed episodes: {len(removed)}",
        f"Delivered episodes: {len(kept)}",
        f"Instruction corrections: {len(store.manifest.get('instruction_overrides') or {})}",
    ]
    report_path = _write_delivery_report(store, batch_id=batch_id, report_lines=report_lines)

    finalized, finalize_error = _try_finalize(output_root)
    result: dict[str, Any] = {
        "output_root": str(output_root),
        "kept_episodes": len(kept),
        "removed_episodes": len(removed),
        "finalized": finalized,
        "rebuild_mode": "legacy",
        "report_path": str(report_path),
    }
    if finalize_error:
        result["finalize_error"] = finalize_error
    return result


def rebuild_delivery_dataset(
    *,
    state: DatasetState,
    store: QcStore,
    output_root: Path,
    batch_id: str,
    job_id: str,
    mode: str | None = None,
) -> dict[str, Any]:
    rebuild_mode = _resolve_rebuild_mode(mode)
    if rebuild_mode == "legacy":
        return _rebuild_legacy_delivery_dataset(
            state=state,
            store=store,
            output_root=output_root,
            batch_id=batch_id,
            job_id=job_id,
        )
    return _rebuild_minimal_delivery_dataset(
        state=state,
        store=store,
        output_root=output_root,
        batch_id=batch_id,
        job_id=job_id,
    )


def start_rebuild_job(
    *,
    state: DatasetState,
    store: QcStore,
    delivery_root: Path,
    batch_id: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    active = get_active_rebuild_job(store)
    if active and active.get("status") in ("queued", "running"):
        raise ValueError(f"Rebuild already in progress: {active.get('job_id')}")

    job_id = uuid.uuid4().hex[:12]
    _register_running_dataset(state.dataset_root, job_id)
    batch = batch_id or datetime.now(timezone.utc).strftime("%Y%m%d")
    task_name = state.dataset_root.name
    try:
        batch, output_root = allocate_delivery_path(delivery_root, task_name, batch)
    except Exception:
        _unregister_running_dataset(state.dataset_root, job_id)
        raise

    job_record = {
        "job_id": job_id,
        "status": "queued",
        "created_at": _utc_now(),
        "batch_id": batch,
        "output_root": str(output_root),
        "rebuild_mode": _resolve_rebuild_mode(mode),
    }
    store.register_rebuild_job(job_record)
    _set_job(job_id, **_job_updates(job_record))

    def _worker() -> None:
        started = _utc_now()
        _sync_job(job_id, store, status="running", started_at=started)
        try:
            result = rebuild_delivery_dataset(
                state=state,
                store=store,
                output_root=output_root,
                batch_id=batch,
                job_id=job_id,
                mode=mode,
            )
            finished = {
                "status": "completed",
                "finished_at": _utc_now(),
                **result,
            }
            _sync_job(job_id, store, **finished)
            store.append_audit(episode_index=None, action="rebuild_completed", after=finished)
        except Exception as exc:
            logger.exception("Rebuild job %s failed", job_id)
            _cleanup_incomplete_output(output_root)
            failed = {
                "status": "failed",
                "finished_at": _utc_now(),
                "error": str(exc),
            }
            _sync_job(job_id, store, **failed)
            store.append_audit(episode_index=None, action="rebuild_failed", after=failed)
        finally:
            _unregister_running_dataset(state.dataset_root, job_id)

    thread = threading.Thread(target=_worker, name=f"qc-rebuild-{job_id}", daemon=True)
    thread.start()
    return job_record
