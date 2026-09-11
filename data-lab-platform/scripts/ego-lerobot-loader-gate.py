#!/usr/bin/env python3
"""Official LeRobot v3 loader gate — init is not enough.

Validates layout, timestamp/index semantics, and actual frame sampling via the
locked official LeRobot reader (no loader patches, no relaxed tolerance).

Sampling tiers (commercial ego platform):
  head_mid_tail (default) — candidate / PR gate; ~12 samples/dataset, dual backend.
  all — optional release regression only; O(frames) video decode, run offline/nightly.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _git_head(repo: Path) -> str | None:
    if not (repo / ".git").exists():
        return None
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _bootstrap_ego_platform(datalab_root: Path) -> None:
    candidates = [
        datalab_root.parent / "ego-platform" / "src",
        Path("/ego-platform/src"),
    ]
    for candidate in candidates:
        if (candidate / "ego_platform").is_dir():
            sys.path.insert(0, str(candidate))
            return


def _bootstrap_lerobot(lerobot_repo: Path) -> dict[str, str]:
    src = lerobot_repo / "src"
    if not src.is_dir():
        raise FileNotFoundError(f"lerobot src not found: {src}")
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    import lerobot  # noqa: WPS433

    return {
        "lerobot_version": str(getattr(lerobot, "__version__", "unknown")),
        "lerobot_commit": _git_head(lerobot_repo) or "",
        "lerobot_repo": str(lerobot_repo.resolve()),
    }


def _video_keys_from_info(info: dict[str, Any]) -> list[str]:
    feats = info.get("features") or {}
    return sorted(k for k, spec in feats.items() if isinstance(spec, dict) and spec.get("dtype") == "video")


def _episode_bounds(root: Path, info: dict[str, Any]) -> list[dict[str, Any]]:
    data_files = sorted((root / "data").rglob("*.parquet"))
    if not data_files:
        raise FileNotFoundError(f"no data parquet under {root / 'data'}")
    episodes: dict[int, dict[str, Any]] = {}
    for pq_path in data_files:
        table = pq.read_table(pq_path)
        if "episode_index" not in table.column_names:
            continue
        ep_vals = table["episode_index"].to_pylist()
        fi_vals = table["frame_index"].to_pylist() if "frame_index" in table.column_names else None
        ts_vals = table["timestamp"].to_pylist() if "timestamp" in table.column_names else None
        idx_vals = table["index"].to_pylist() if "index" in table.column_names else None
        for row_i, ep in enumerate(ep_vals):
            ep = int(ep or 0)
            ent = episodes.setdefault(
                ep,
                {"episode_index": ep, "global_indices": [], "frame_indices": [], "timestamps": []},
            )
            gidx = int(idx_vals[row_i]) if idx_vals is not None else len(ent["global_indices"])
            ent["global_indices"].append(gidx)
            if fi_vals is not None:
                ent["frame_indices"].append(int(fi_vals[row_i] or 0))
            if ts_vals is not None:
                ent["timestamps"].append(float(ts_vals[row_i] or 0.0))
    fps = float(info.get("fps") or 30.0)
    out: list[dict[str, Any]] = []
    for ep in sorted(episodes):
        ent = episodes[ep]
        gindices = ent["global_indices"]
        fis = ent["frame_indices"]
        tss = ent["timestamps"]
        out.append(
            {
                "episode_index": ep,
                "length": len(gindices),
                "global_start": min(gindices),
                "global_end": max(gindices),
                "frame_index_min": min(fis) if fis else None,
                "frame_index_max": max(fis) if fis else None,
                "timestamp_min": round(min(tss), 6) if tss else None,
                "timestamp_max": round(max(tss), 6) if tss else None,
                "expected_timestamp_max": round((len(gindices) - 1) / fps, 6) if fps > 0 else None,
                "timestamp_semantics_ok": (
                    tss
                    and max(tss) <= (len(gindices) - 1) / fps + 1.0 / fps
                    and min(tss) >= -1e-6
                )
                if tss
                else None,
            }
        )
    return out


def _sample_indices(length: int, start: int, mode: str) -> list[int]:
    if mode == "all":
        return list(range(start, start + length))
    picks = {start, start + length // 2, start + length - 1}
    if length <= 3:
        picks = set(range(start, start + length))
    return sorted(picks)


def _audit_timestamp_semantics(episodes: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for ep in episodes:
        if ep.get("timestamp_semantics_ok") is False:
            errors.append(
                f"episode {ep['episode_index']}: timestamp range "
                f"[{ep['timestamp_min']}, {ep['timestamp_max']}] exceeds episode-local "
                f"expected max {ep['expected_timestamp_max']} (length={ep['length']})"
            )
        fi_min, fi_max = ep.get("frame_index_min"), ep.get("frame_index_max")
        if fi_min is not None and (fi_min != 0 or fi_max != ep["length"] - 1):
            errors.append(
                f"episode {ep['episode_index']}: frame_index [{fi_min}, {fi_max}] "
                f"expected [0, {ep['length'] - 1}]"
            )
    return errors


def run_loader_regression(
    root: Path,
    *,
    lerobot_repo: Path,
    video_backends: list[str],
    sample_mode: str,
    run_dataloader: bool,
) -> dict[str, Any]:
    root = root.resolve()
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        return {"ok": False, "errors": [f"missing {info_path}"], "root": str(root)}

    info = json.loads(info_path.read_text(encoding="utf-8"))
    codebase_version = str(info.get("codebase_version") or "")
    layout_errors: list[str] = []
    if codebase_version not in ("v3.0", "v3.1"):
        layout_errors.append(f"unexpected codebase_version: {codebase_version or '(empty)'}")
    if not (root / "meta" / "tasks.parquet").is_file():
        layout_errors.append("missing meta/tasks.parquet")

    env_info = _bootstrap_lerobot(lerobot_repo)
    pin = os.environ.get("LEROBOT_PIN_COMMIT", "").strip()
    if pin:
        env_info["lerobot_pin_commit"] = pin

    from lerobot.datasets.lerobot_dataset import LeRobotDataset  # noqa: WPS433

    episodes = _episode_bounds(root, info)
    audit_errors = _audit_timestamp_semantics(episodes)
    video_keys = _video_keys_from_info(info)

    report: dict[str, Any] = {
        "generated_at": _utc_now(),
        "root": str(root),
        "dataset": {
            "total_episodes": info.get("total_episodes"),
            "total_frames": info.get("total_frames"),
            "fps": info.get("fps"),
            "codebase_version": info.get("codebase_version"),
        },
        "environment": env_info,
        "episodes": episodes,
        "pre_audit_errors": audit_errors,
        "video_keys": video_keys,
        "backends": {},
        "ok": False,
        "errors": [],
    }

    all_errors: list[str] = list(layout_errors) + list(audit_errors)

    for backend in video_backends:
        backend_report: dict[str, Any] = {
            "video_backend": backend,
            "init_ok": False,
            "len": None,
            "samples": [],
            "failures": [],
        }
        try:
            ds = LeRobotDataset(repo_id=root.name, root=root, video_backend=backend)
            backend_report["init_ok"] = True
            backend_report["len"] = len(ds)
            if backend_report["len"] != info.get("total_frames"):
                all_errors.append(
                    f"{backend}: len(ds)={backend_report['len']} != info.total_frames={info.get('total_frames')}"
                )

            for ep in episodes:
                gstart = int(ep["global_start"])
                length = int(ep["length"])
                for gidx in _sample_indices(length, gstart, sample_mode):
                    sample_rec: dict[str, Any] = {"global_index": gidx, "episode_index": ep["episode_index"], "ok": False}
                    try:
                        item = ds[gidx]
                        sample_rec["keys"] = sorted(item.keys())
                        missing = []
                        for vk in video_keys:
                            if vk not in item:
                                missing.append(vk)
                                continue
                            shape = tuple(item[vk].shape) if hasattr(item[vk], "shape") else None
                            sample_rec[f"{vk}.shape"] = shape
                            if shape is None or len(shape) < 3:
                                missing.append(f"{vk}:bad_shape")
                        if missing:
                            raise RuntimeError(f"missing or invalid video keys: {missing}")
                        sample_rec["ok"] = True
                    except Exception as exc:
                        sample_rec["error"] = str(exc)
                        backend_report["failures"].append(sample_rec)
                        all_errors.append(f"{backend} ep{ep['episode_index']} idx={gidx}: {exc}")
                    backend_report["samples"].append(sample_rec)

            if run_dataloader and backend_report["init_ok"]:
                try:
                    from torch.utils.data import DataLoader

                    loader = DataLoader(ds, batch_size=2, shuffle=False, num_workers=0)
                    batch = next(iter(loader))
                    backend_report["dataloader_ok"] = True
                    backend_report["dataloader_batch_keys"] = sorted(batch.keys())
                except Exception as exc:
                    backend_report["dataloader_ok"] = False
                    backend_report["dataloader_error"] = str(exc)
                    all_errors.append(f"{backend} DataLoader: {exc}")

        except Exception as exc:
            backend_report["init_error"] = str(exc)
            backend_report["init_traceback"] = traceback.format_exc()
            all_errors.append(f"{backend} init: {exc}")

        report["backends"][backend] = backend_report

    report["errors"] = all_errors
    report["ok"] = not all_errors
    report["failure_count"] = sum(len(b.get("failures") or []) for b in report["backends"].values())
    return report


def validate_dataset_root(
    root: Path,
    *,
    lerobot_repo: Path | None = None,
    sample_mode: str = "head_mid_tail",
    video_backends: list[str] | None = None,
    run_dataloader: bool = True,
) -> dict:
    if lerobot_repo is None:
        lerobot_repo = Path(__file__).resolve().parents[2].parent / "lerobot"
    try:
        return run_loader_regression(
            root,
            lerobot_repo=lerobot_repo,
            video_backends=video_backends or ["pyav", "torchcodec"],
            sample_mode=sample_mode,
            run_dataloader=run_dataloader,
        )
    except Exception as exc:
        return {"ok": False, "errors": [f"loader regression failed: {exc}"], "root": str(root.resolve())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", help="Dataset root directories to validate")
    parser.add_argument("--datalab-root", type=Path, default=None)
    parser.add_argument("--lerobot-repo", type=Path, default=None)
    parser.add_argument("--video-backends", nargs="+", default=["pyav", "torchcodec"])
    parser.add_argument("--sample", choices=("head_mid_tail", "all"), default="head_mid_tail")
    parser.add_argument("--dataloader", action="store_true", default=True)
    parser.add_argument("--no-dataloader", action="store_false", dest="dataloader")
    parser.add_argument("--report", type=Path, help="Write JSON report (excludes manifest self-hash)")
    args = parser.parse_args(argv)

    if args.datalab_root is None:
        here = Path(__file__).resolve()
        args.datalab_root = next(
            (p for p in here.parents if p.name == "data-lab-platform"),
            here.parent if here.parent.name == "scripts" else Path("/app"),
        )
    _bootstrap_ego_platform(args.datalab_root)

    lerobot_repo = args.lerobot_repo or (args.datalab_root.parent / "lerobot")
    reports: list[dict[str, Any]] = []
    failed = 0
    for raw in args.roots:
        result = validate_dataset_root(
            Path(raw),
            lerobot_repo=lerobot_repo,
            sample_mode=args.sample,
            video_backends=args.video_backends,
            run_dataloader=args.dataloader,
        )
        reports.append(result)
        status = "OK" if result["ok"] else "FAIL"
        print(f"[{status}] {result.get('root', raw)}")
        for err in result.get("errors") or []:
            print(f"  - {err}")
        if not result["ok"]:
            failed += 1

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        payload = {"generated_at": _utc_now(), "reports": reports}
        args.report.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"report: {args.report}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
