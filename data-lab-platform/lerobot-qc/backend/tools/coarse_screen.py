#!/usr/bin/env python3
"""Offline coarse screening for LeRobot v3.0 datasets — outputs JSON for lerobot-qc import."""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import HTTPException

from dataset_manager import load_local_dataset  # noqa: E402
from qc_metrics import compute_qc_metrics, dt_max_threshold_ms, read_episode_frames  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _detect_frozen_trajectory(state, episode_index: int, *, min_frames: int = 20) -> bool:
    df = read_episode_frames(state, episode_index)
    if df.empty or len(df) < min_frames:
        return False
    cols = [c for c in df.columns if c in ("action", "observation.state")]
    if not cols:
        return False
    for col in cols:
        series = df[col]
        if col == "action" or col == "observation.state":
            try:
                stacked = np.stack([np.asarray(row, dtype=float).reshape(-1) for row in series.tolist()])
            except (ValueError, TypeError):
                continue
            if stacked.shape[0] < min_frames:
                continue
            diffs = np.linalg.norm(np.diff(stacked, axis=0), axis=1)
            if float(np.max(diffs)) < 1e-5 and float(np.mean(diffs)) < 1e-6:
                return True
        else:
            arr = np.asarray(series.tolist(), dtype=float)
            if arr.ndim == 1 and len(arr) >= min_frames:
                diffs = np.abs(np.diff(arr))
                if float(np.max(diffs)) < 1e-5:
                    return True
    return False


def screen_episode(state, episode_index: int) -> dict[str, Any]:
    metrics = compute_qc_metrics(state, episode_index)
    flags = list(metrics.get("anomalies") or [])
    auto_reject = False
    suspicious = False
    reasons: list[str] = []

    if metrics["duration_sec"] < 1.0:
        auto_reject = True
        reasons.append("duration_lt_1s")
    if metrics["dt_max_ms"] > metrics.get("dt_max_threshold_ms", dt_max_threshold_ms(state.fps)):
        suspicious = True
        reasons.append("dt_max_exceeds_threshold")
    if metrics.get("has_time_gap"):
        suspicious = True
        reasons.append("time_gap")
    if not metrics.get("frame_rate_stable", True):
        suspicious = True
        reasons.append("unstable_frame_rate")

    if _detect_frozen_trajectory(state, episode_index):
        auto_reject = True
        reasons.append("trajectory_frozen")

    return {
        "episode_index": episode_index,
        "metrics": metrics,
        "flags": sorted(set(flags + reasons)),
        "auto_reject": auto_reject,
        "suspicious": suspicious and not auto_reject,
        "reason": "; ".join(reasons),
    }


def build_screening_report(
    dataset_path: Path,
    *,
    sample_rate: float = 0.05,
    premium_sample_rate: float = 0.15,
    premium: bool = False,
    seed: int = 42,
) -> dict[str, Any]:
    try:
        state = load_local_dataset(str(dataset_path))
    except HTTPException as exc:
        raise SystemExit(f"Failed to load dataset: {exc.detail}") from exc
    all_indices = [int(x) for x in state.episodes_df["episode_index"].tolist()]
    rate = premium_sample_rate if premium else sample_rate

    suspicious_episodes: list[dict[str, Any]] = []
    auto_reject_episodes: list[dict[str, Any]] = []
    screened: list[dict[str, Any]] = []

    rng = random.Random(seed)

    for ep_idx in all_indices:
        result = screen_episode(state, ep_idx)
        screened.append(result)
        if result["auto_reject"]:
            auto_reject_episodes.append(
                {
                    "episode_index": ep_idx,
                    "reason": result["reason"] or "auto_reject",
                    "flags": result["flags"],
                }
            )
        elif result["suspicious"]:
            suspicious_episodes.append(
                {
                    "episode_index": ep_idx,
                    "reason": result["reason"] or "suspicious",
                    "flags": result["flags"],
                }
            )

    flagged_indices = {x["episode_index"] for x in suspicious_episodes} | {
        x["episode_index"] for x in auto_reject_episodes
    }
    remaining = [idx for idx in all_indices if idx not in flagged_indices]
    sample_n = max(1, int(len(all_indices) * rate)) if all_indices else 0
    random_sample = sorted(rng.sample(remaining, min(sample_n, len(remaining)))) if remaining else []

    for ep_idx in random_sample:
        if ep_idx in flagged_indices:
            continue
        suspicious_episodes.append(
            {
                "episode_index": ep_idx,
                "reason": "random_qc_sample",
                "flags": ["random_sample"],
            }
        )

    return {
        "version": 1,
        "generated_at": _utc_now(),
        "source": "lerobot-qc coarse_screen",
        "batch_id": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "dataset_root": str(state.dataset_root),
        "codebase_version": state.info.get("codebase_version"),
        "total_episodes": len(all_indices),
        "sample_rate": rate,
        "suspicious_episodes": suspicious_episodes,
        "auto_reject_episodes": auto_reject_episodes,
        "summary": {
            "auto_reject_count": len(auto_reject_episodes),
            "suspicious_count": len(suspicious_episodes),
            "random_sample_count": len(random_sample),
            "screened_count": len(screened),
        },
        "episodes_screened": screened,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Coarse screening for LeRobot v3.0 QC import")
    parser.add_argument("dataset_path", type=Path, help="Path to LeRobot v3.0 dataset root")
    parser.add_argument("-o", "--output", type=Path, help="Write JSON report (default: stdout)")
    parser.add_argument("--sample-rate", type=float, default=0.05, help="Random QC sample rate (default 5%%)")
    parser.add_argument("--premium", action="store_true", help="Use 15%% sample rate for premium orders")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--compact", action="store_true", help="Omit per-episode screened details")
    args = parser.parse_args()

    report = build_screening_report(
        args.dataset_path.expanduser().resolve(),
        sample_rate=args.sample_rate,
        premium=args.premium,
        seed=args.seed,
    )
    if args.compact:
        report.pop("episodes_screened", None)

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
        print(
            f"auto_reject={report['summary']['auto_reject_count']} "
            f"suspicious={report['summary']['suspicious_count']} "
            f"random_sample={report['summary']['random_sample_count']}"
        )
    else:
        print(text)


if __name__ == "__main__":
    main()
