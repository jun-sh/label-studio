#!/usr/bin/env bash
# ego-pipeline-performance.env.sh — P1 derive + convert throughput defaults (ego-001 production).
# Sourced by ego-run-pipeline / ego-process. Override via environment before invoke.
#
# Collection SLA: upload → derive READY (target P95 < 30s per session with P1 derive tuning).
# Egodome SLA: convert is async batch; not on operator critical path.

# --- derive (34 docker derive-worker; set before compose up / recreate) ---
export DERIVE_EXTRACT_CONCURRENCY="${DERIVE_EXTRACT_CONCURRENCY:-4}"
export DERIVE_EXTRACT_ENABLED="${DERIVE_EXTRACT_ENABLED:-1}"
export DERIVE_FFPROBE_HEARTBEAT="${DERIVE_FFPROBE_HEARTBEAT:-1}"
export DERIVE_MCAP_SINGLE_FAST="${DERIVE_MCAP_SINGLE_FAST:-1}"

# --- convert (34 host ego-process) ---
export EGO_WILOR_WARM="${EGO_WILOR_WARM:-1}"
# 0 = per-session convert.py (production default; convert_batch CLI not shipped yet).
export EGO_CONVERT_BATCH="${EGO_CONVERT_BATCH:-0}"
export EGO_WILOR_DEVICE="${EGO_WILOR_DEVICE:-cuda:0}"
export EGO_WILOR_REQUIRE_CUDA="${EGO_WILOR_REQUIRE_CUDA:-1}"
export EGO_USE_CONVERT_WORKER="${EGO_USE_CONVERT_WORKER:-1}"
# Single GPU: keep 1; raise to 2–4 only with multiple GPUs or CPU-only WiLoR.
export EGO_CONVERT_PARALLEL="${EGO_CONVERT_PARALLEL:-1}"

# Sparse WiLoR inference (production default stride=3; set 1 for QC/debug).
export EGO_HANDS_FRAME_STRIDE="${EGO_HANDS_FRAME_STRIDE:-3}"

# Depth preview on by default in production (Viewer overlay on front_left; set 0 to skip).
export EGO_DEPTH_PREVIEW="${EGO_DEPTH_PREVIEW:-1}"
export EGO_DEPTH_PREVIEW_STRIDE="${EGO_DEPTH_PREVIEW_STRIDE:-30}"
export EGO_DEPTH_PREVIEW_MAX_FRAMES="${EGO_DEPTH_PREVIEW_MAX_FRAMES:-120}"

# HuggingFace hub — Depth-Anything V2 (34 通常无法直连 huggingface.co)
_ego_pipeline_datalab_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HUGGINGFACE_HUB_ENDPOINT="${HUGGINGFACE_HUB_ENDPOINT:-${HF_ENDPOINT}}"
export HF_HOME="${HF_HOME:-${_ego_pipeline_datalab_root}/data-storage/cache/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-${HF_HOME}/hub}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-${HUGGINGFACE_HUB_CACHE}}"
export HF_HUB_DISABLE_TELEMETRY="${HF_HUB_DISABLE_TELEMETRY:-1}"
unset _ego_pipeline_datalab_root

# SLO-B: batch convert may exceed 10min per session; 3600s for operational validation.
export EGO_CONVERT_SLO_B_SEC="${EGO_CONVERT_SLO_B_SEC:-3600}"
