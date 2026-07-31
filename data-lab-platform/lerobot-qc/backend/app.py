"""LeRobot QC terminal inspection API — read-only source dataset, sidecar audit state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dataset_catalog import datasets_root, get_collection, list_collections, resolve_package_path, validate_dataset_path
from dataset_manager import DatasetState, load_local_dataset
from qc_metrics import build_trajectory_payload, compute_qc_metrics
from qc_store import QcStore
from rebuild_service import (
    find_rebuild_job_in_sidecar,
    get_active_rebuild_job,
    get_job,
    hydrate_jobs_from_store,
    resolve_rebuild_job,
    start_rebuild_job,
)
from screening_service import import_screening_payload
from video_service import configure_cache, iter_file_range, parse_range, resolve_stream_path

APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "static"
CACHE_ROOT = Path(os.environ.get("LEROBOT_QC_CACHE", "/tmp/lerobot_qc_cache"))
SIDECAR_ROOT = Path(os.environ.get("LEROBOT_QC_SIDECAR", "/data/qc-sidecar"))
DELIVERY_ROOT = Path(os.environ.get("LEROBOT_QC_DELIVERY", "/data/qc-delivery"))
DEFAULT_OPERATOR = os.environ.get("LEROBOT_QC_OPERATOR", "anonymous")

configure_cache(CACHE_ROOT)

app = FastAPI(title="LeRobot QC", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

state: DatasetState | None = None
store: QcStore | None = None


class DatasetLoadRequest(BaseModel):
    local_path: str
    video_key: str | None = None
    operator_id: str | None = None


class ReviewRequest(BaseModel):
    episode_index: int
    status: Literal["approved", "rejected", "suspicious", "pending"]
    reason: str | None = None
    note: str | None = None


class InstructionRequest(BaseModel):
    episode_index: int
    instruction: str
    original_instruction: str | None = None


class RebuildRequest(BaseModel):
    batch_id: str | None = None


class ScreeningImportRequest(BaseModel):
    suspicious_episodes: list[Any] = []
    auto_reject_episodes: list[Any] = []
    episodes: list[Any] = []
    rejected_episodes: list[Any] = []
    source: str | None = None
    batch_id: str | None = None


def _attach_qc_summary(summary: dict[str, Any], ds: DatasetState, qc: QcStore) -> dict[str, Any]:
    all_indices = [int(x) for x in ds.episodes_df["episode_index"].tolist()]
    summary["review_summary"] = qc.review_summary(all_indices)
    summary["datasets_root"] = str(datasets_root())
    active_job = get_active_rebuild_job(qc)
    if active_job:
        summary["active_rebuild_job"] = active_job
    return summary


def _require_state() -> DatasetState:
    if state is None:
        raise HTTPException(status_code=400, detail="Dataset not loaded")
    return state


def _require_store() -> QcStore:
    if store is None:
        raise HTTPException(status_code=400, detail="QC store not initialized")
    return store


def _episode_instruction(ds: DatasetState, episode_index: int) -> str:
    qc = _require_store()
    override = qc.get_instruction_override(episode_index)
    if override:
        return override
    row = ds.episode_row(episode_index)
    text, _ = ds.resolve_language_instruction(row)
    return text


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "lerobot-qc"})


@app.get("/")
def root() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        return HTMLResponse("<h1>LeRobot QC</h1><p>Missing static UI</p>")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.post("/api/dataset/load")
def api_load_dataset(req: DatasetLoadRequest) -> JSONResponse:
    global state, store
    try:
        validate_dataset_path(req.local_path)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 403
        raise HTTPException(status_code=status, detail=detail) from exc
    loaded = load_local_dataset(req.local_path, video_key=req.video_key)
    state = loaded
    operator = req.operator_id or DEFAULT_OPERATOR
    store = QcStore.open(loaded.dataset_root, SIDECAR_ROOT, operator_id=operator)
    hydrate_jobs_from_store(store)
    summary = loaded.build_summary()
    summary["sidecar_root"] = str(store.sidecar_root)
    summary["removed_count"] = len(store.removed_episode_indices())
    summary["qc_state"] = _qc_episode_map(loaded, store)
    return JSONResponse(_attach_qc_summary(summary, loaded, store))


@app.get("/api/dataset/info")
def api_dataset_info() -> JSONResponse:
    ds = _require_state()
    qc = _require_store()
    summary = ds.build_summary()
    summary["sidecar_root"] = str(qc.sidecar_root)
    summary["removed_count"] = len(qc.removed_episode_indices())
    summary["qc_state"] = _qc_episode_map(ds, qc)
    return JSONResponse(_attach_qc_summary(summary, ds, qc))


@app.get("/api/datasets/collections")
def api_list_collections() -> JSONResponse:
    collections = list_collections()
    summaries = [
        {
            "id": c.get("id"),
            "title": c.get("title"),
            "description": c.get("description"),
            "robot_type": c.get("robot_type"),
            "package_count": c.get("package_count", len(c.get("packages") or [])),
            "kind": c.get("kind", "collection"),
        }
        for c in collections
    ]
    return JSONResponse({"collections": summaries, "datasets_root": str(datasets_root())})


@app.get("/api/datasets/collections/{collection_id}")
def api_get_collection(collection_id: str) -> JSONResponse:
    try:
        collection = get_collection(collection_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Collection not found: {collection_id}") from exc
    return JSONResponse(collection)


@app.get("/api/datasets/resolve")
def api_resolve_package(collection: str, package: str) -> JSONResponse:
    try:
        local_path = resolve_package_path(collection, package)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse({"collection_id": collection, "package_id": package, "local_path": local_path})


def _qc_episode_map(ds: DatasetState, qc: QcStore) -> dict[str, Any]:
    removed = qc.removed_episode_indices()
    payload: dict[str, Any] = {}
    for ep_idx in ds.episodes_df["episode_index"].tolist():
        ep_idx = int(ep_idx)
        review = qc.get_review(ep_idx)
        payload[str(ep_idx)] = {
            "review": review,
            "language_instruction": _episode_instruction(ds, ep_idx),
            "is_removed": ep_idx in removed,
        }
    return payload


@app.get("/api/episodes/{episode_index}")
def api_episode_detail(episode_index: int) -> JSONResponse:
    ds = _require_state()
    qc = _require_store()
    row = ds.episode_row(episode_index)
    length = ds.episode_length(row)
    instruction = _episode_instruction(ds, episode_index)
    original_row_text, task_index = ds.resolve_language_instruction(row)
    review = qc.get_review(episode_index)
    is_removed = episode_index in qc.removed_episode_indices()
    return JSONResponse(
        {
            "episode_index": episode_index,
            "length": length,
            "duration": length / ds.fps if ds.fps else 0.0,
            "language_instruction": instruction,
            "original_language_instruction": original_row_text,
            "task_index": task_index,
            "review": review,
            "is_removed": is_removed,
            "removal_reason": review.get("reason") if is_removed else None,
            "metrics": compute_qc_metrics(ds, episode_index),
            "video_keys": ds.video_keys(),
            "selected_video_key": ds.video_key,
        }
    )


@app.get("/api/episodes/{episode_index}/trajectory")
def api_episode_trajectory(episode_index: int) -> JSONResponse:
    ds = _require_state()
    _require_store()
    return JSONResponse(build_trajectory_payload(ds, episode_index))


@app.get("/api/episodes/{episode_index}/metrics")
def api_episode_metrics(episode_index: int) -> JSONResponse:
    ds = _require_state()
    return JSONResponse(compute_qc_metrics(ds, episode_index))


@app.get("/api/episodes/{episode_index}/video_timing")
def api_video_timing(episode_index: int, video_key: str | None = None) -> JSONResponse:
    ds = _require_state()
    video_key = video_key or ds.video_key
    offsets = ds.video_offsets(video_key or "", episode_index)
    row = ds.episode_row(episode_index)
    length = ds.episode_length(row)
    return JSONResponse(
        {
            "episode_index": episode_index,
            "fps": ds.fps,
            "length": length,
            "duration": length / ds.fps if ds.fps else 0.0,
            **offsets,
        }
    )


@app.get("/api/video/{episode_index}")
def api_stream_video(episode_index: int, request: Request, video_key: str | None = None) -> Response:
    ds = _require_state()
    _require_store()
    path = resolve_stream_path(ds, episode_index, video_key=video_key)
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    if range_header:
        byte_range = parse_range(range_header, file_size)
        if not byte_range:
            return Response(status_code=416)
        start, end = byte_range
        length = end - start + 1
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": "video/mp4",
        }
        return StreamingResponse(
            iter_file_range(path, start, length),
            status_code=206,
            media_type="video/mp4",
            headers=headers,
        )

    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
    )


@app.get("/api/qc/state")
def api_qc_state() -> JSONResponse:
    qc = _require_store()
    return JSONResponse(qc.export_state())


@app.post("/api/qc/review")
def api_qc_review(req: ReviewRequest) -> JSONResponse:
    ds = _require_state()
    qc = _require_store()
    ds.episode_row(req.episode_index)
    if req.status == "rejected" and not (req.reason or "").strip():
        raise HTTPException(status_code=400, detail="Removal reason is required for rejected episodes")
    record = qc.set_review(req.episode_index, req.status, reason=req.reason, note=req.note)
    return JSONResponse({"ok": True, "episode_index": req.episode_index, "review": record})


@app.post("/api/qc/instruction")
def api_qc_instruction(req: InstructionRequest) -> JSONResponse:
    ds = _require_state()
    qc = _require_store()
    if req.episode_index in qc.removed_episode_indices():
        raise HTTPException(status_code=400, detail="Cannot edit instruction for a removed episode")
    instruction = req.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="Instruction cannot be empty")
    row = ds.episode_row(req.episode_index)
    original = req.original_instruction
    if not original:
        original, _ = ds.resolve_language_instruction(row)
    result = qc.set_instruction_override(
        req.episode_index,
        instruction,
        original_instruction=original or "",
    )
    current_status = qc.get_review(req.episode_index).get("status") or "pending"
    if current_status == "pending":
        qc.set_review(req.episode_index, "approved", note="instruction corrected")
    review = qc.get_review(req.episode_index)
    return JSONResponse({"ok": True, **result, "review": review})


@app.post("/api/qc/screening/run")
def api_run_screening(premium: bool = False, sample_rate: float | None = None) -> JSONResponse:
    """Run coarse screening on the currently loaded dataset (server-side)."""
    from tools.coarse_screen import build_screening_report

    ds = _require_state()
    report = build_screening_report(
        ds.dataset_root,
        sample_rate=sample_rate or 0.05,
        premium=premium,
    )
    compact = {
        k: report[k]
        for k in report
        if k != "episodes_screened"
    }
    return JSONResponse({"ok": True, **compact})


@app.post("/api/qc/screening/import")
def api_import_screening(payload: ScreeningImportRequest) -> JSONResponse:
    qc = _require_store()
    _require_state()
    result = import_screening_payload(qc, payload.dict(exclude_none=True))
    return JSONResponse({"ok": True, **result})


@app.get("/api/qc/summary")
def api_qc_summary() -> JSONResponse:
    ds = _require_state()
    qc = _require_store()
    all_indices = [int(x) for x in ds.episodes_df["episode_index"].tolist()]
    return JSONResponse(qc.review_summary(all_indices))


@app.get("/api/qc/audit")
def api_qc_audit(limit: int = 200) -> JSONResponse:
    qc = _require_store()
    return JSONResponse({"entries": qc.read_audit_entries(limit=limit)})


@app.post("/api/qc/rebuild")
def api_qc_rebuild(req: RebuildRequest) -> JSONResponse:
    ds = _require_state()
    qc = _require_store()
    DELIVERY_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        job = start_rebuild_job(state=ds, store=qc, delivery_root=DELIVERY_ROOT, batch_id=req.batch_id)
    except ValueError as exc:
        if "already in progress" in str(exc).lower():
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, **job})


@app.get("/api/qc/rebuild/{job_id}")
def api_qc_rebuild_status(job_id: str) -> JSONResponse:
    qc = store
    job = resolve_rebuild_job(job_id, store=qc, sidecar_root=SIDECAR_ROOT)
    if not job:
        raise HTTPException(status_code=404, detail=f"Rebuild job not found: {job_id}")
    return JSONResponse(job)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
