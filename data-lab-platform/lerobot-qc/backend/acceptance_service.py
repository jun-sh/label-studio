"""Acceptance check of a finished delivery dataset against its source and the QC decisions.

Runs inside the QC container, which has no lerobot package and no PyAV: parquet is read with
pandas/pyarrow, videos are decoded with ffprobe (the same ffmpeg build that wrote them).

Checks:
- delivered episodes == source episodes minus rejected; frames == sum of kept source lengths
- per kept episode the task text (frame task_index -> meta/tasks.parquet) equals the
  instruction override if any, else the source task text
- frame_index contiguous per episode, global index contiguous
- every RGB (info.json video_path) and depth (meta/depth_features.json video_paths) segment
  exists and its decoded timestamps cover from_timestamp + k/fps within 0.5/fps;
  depth decodes to a 16-bit pixel format
- meta/stats.json action count == total frames
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from depth_split import DEPTH_SIDECAR

DEFAULT_VIDEO_PATH = "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
MAX_PROBLEMS = 20
DECODE_TIMEOUT_S = int(os.environ.get("LEROBOT_QC_ACCEPTANCE_DECODE_TIMEOUT", "3600"))
_SIXTEEN_BIT = re.compile(r"16(le|be)$")

ProgressFn = Callable[[int, int], None]


def acceptance_enabled() -> bool:
    """LEROBOT_QC_ACCEPTANCE=0/false/off skips the check (read per call so tests can toggle it)."""
    value = os.environ.get("LEROBOT_QC_ACCEPTANCE", "1").strip().lower()
    return value not in ("0", "false", "off", "no")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_frames(root: Path, columns: list[str]) -> pd.DataFrame:
    files = sorted((root / "data").rglob("*.parquet"))
    if not files:
        return pd.DataFrame({col: pd.Series(dtype="int64") for col in columns})
    return pd.concat([pd.read_parquet(path, columns=columns) for path in files], ignore_index=True)


def _read_episodes(root: Path) -> pd.DataFrame:
    files = sorted((root / "meta" / "episodes").rglob("*.parquet"))
    if not files:
        return pd.DataFrame({"episode_index": pd.Series(dtype="int64")})
    df = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    return df.sort_values("episode_index").reset_index(drop=True)


def _read_tasks(root: Path) -> dict[int, str]:
    path = root / "meta" / "tasks.parquet"
    if not path.is_file():
        return {}
    df = pd.read_parquet(path)
    return {int(idx): str(text) for text, idx in zip(df.index, df["task_index"])}


def decode_video(path: Path) -> tuple[np.ndarray, str]:
    """Decode every frame of the first video stream; returns (sorted timestamps, pixel format)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "frame=best_effort_timestamp_time,pts_time,pix_fmt",
        "-of",
        "compact=p=0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=DECODE_TIMEOUT_S)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffprobe failed").strip().splitlines()[-1][:200])
    stamps: list[float] = []
    pix_fmt = ""
    for line in result.stdout.splitlines():
        fields = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
        value = fields.get("best_effort_timestamp_time") or fields.get("pts_time") or ""
        try:
            stamps.append(float(value))
        except ValueError:
            continue
        pix_fmt = pix_fmt or fields.get("pix_fmt", "")
    return np.sort(np.array(stamps, dtype=np.float64)), pix_fmt


class _Problems:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.total = 0

    def add(self, message: str) -> None:
        self.total += 1
        if len(self.items) < MAX_PROBLEMS:
            self.items.append(message)


def check_delivery(
    output_root: Path,
    source_root: Path,
    *,
    removed: set[int],
    overrides: dict[str, str],
    on_progress: ProgressFn | None = None,
    decode: Callable[[Path], tuple[np.ndarray, str]] = decode_video,
) -> dict[str, Any]:
    """Return {ok, checked_at, problems[], ...counts}; never raises for dataset defects."""
    problems = _Problems()
    summary: dict[str, Any] = {}
    try:
        _check(output_root, source_root, removed, overrides, problems, summary, on_progress, decode)
    except Exception as exc:  # a crash is a failed acceptance, not a failed rebuild
        problems.add(f"acceptance check crashed: {type(exc).__name__}: {exc}")
    result: dict[str, Any] = {
        "ok": problems.total == 0,
        "checked_at": _utc_now(),
        "problems": problems.items,
        **summary,
    }
    if problems.total > len(problems.items):
        result["problems_truncated"] = problems.total - len(problems.items)
    return result


def _check(
    out: Path,
    src: Path,
    removed: set[int],
    overrides: dict[str, str],
    problems: _Problems,
    summary: dict[str, Any],
    on_progress: ProgressFn | None,
    decode: Callable[[Path], tuple[np.ndarray, str]],
) -> None:
    info = json.loads((out / "meta" / "info.json").read_text(encoding="utf-8"))
    fps = float(info.get("fps") or 0)
    if fps <= 0:
        problems.add(f"info.json fps invalid: {info.get('fps')!r}")
        return

    src_episodes = _read_episodes(src)
    src_frames = _read_frames(src, ["episode_index", "task_index"])
    src_tasks = _read_tasks(src)
    kept = [int(i) for i in src_episodes["episode_index"].tolist() if int(i) not in removed]

    episodes = _read_episodes(out)
    frames = _read_frames(out, ["episode_index", "frame_index", "index", "task_index"])
    tasks = _read_tasks(out)
    summary.update(episodes=len(episodes), frames=len(frames))

    # -- counts
    if len(episodes) != len(kept):
        problems.add(f"episodes {len(episodes)} != expected {len(kept)}")
    if int(info.get("total_episodes") or 0) != len(kept):
        problems.add(f"info.json total_episodes {info.get('total_episodes')} != expected {len(kept)}")
    src_lengths = src_frames.groupby("episode_index").size()
    expected_frames = int(sum(int(src_lengths.get(orig, 0)) for orig in kept))
    if len(frames) != expected_frames:
        problems.add(f"frames {len(frames)} != expected {expected_frames}")
    if int(info.get("total_frames") or 0) != len(frames):
        problems.add(f"info.json total_frames {info.get('total_frames')} != {len(frames)}")

    # -- task text per episode
    src_by_ep = src_frames.groupby("episode_index")["task_index"].unique()
    out_by_ep = frames.groupby("episode_index")["task_index"].unique()
    task_bad: list[int] = []
    for new, orig in enumerate(kept):
        got = {tasks.get(int(t), f"<missing task {int(t)}>") for t in out_by_ep.get(new, [])}
        override = str(overrides.get(str(orig)) or "").strip()
        want = {override} if override else {src_tasks.get(int(t), f"<missing task {int(t)}>") for t in src_by_ep.get(orig, [])}
        if got != want:
            task_bad.append(orig)
    if task_bad:
        problems.add(f"task text mismatches in {len(task_bad)} episodes (source episodes {task_bad[:5]})")

    # -- index contiguity (frames sorted by global index, as LeRobot reads them)
    frames = frames.sort_values("index", kind="stable").reset_index(drop=True)
    if not np.array_equal(frames["index"].to_numpy(), np.arange(len(frames))):
        problems.add("global index not contiguous from 0")
    expected_fi = frames.groupby("episode_index").cumcount().to_numpy()
    bad_eps = sorted(set(frames.loc[frames["frame_index"].to_numpy() != expected_fi, "episode_index"].tolist()))
    if bad_eps:
        problems.add(f"frame_index not contiguous in {len(bad_eps)} episodes (e.g. {bad_eps[:5]})")

    # -- stats
    stats_path = out / "meta" / "stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.is_file() else {}
    action_count = (stats.get("action") or {}).get("count")
    if action_count != [len(frames)]:
        problems.add(f"stats.json action count {action_count} != [{len(frames)}]")

    # -- videos
    features = info.get("features") or {}
    rgb_keys = [key for key, spec in features.items() if spec.get("dtype") == "video"]
    video_tpl = {key: str(info.get("video_path") or DEFAULT_VIDEO_PATH) for key in rgb_keys}
    depth_keys: list[str] = []
    depth_path = out / "meta" / DEPTH_SIDECAR
    if depth_path.is_file():
        depth = json.loads(depth_path.read_text(encoding="utf-8"))
        depth_keys = list(depth.get("features") or {})
        if set(depth_keys) & set(rgb_keys):
            problems.add("depth keys still listed in info.json features")
        for key in depth_keys:
            video_tpl[key] = str((depth.get("video_paths") or {}).get(key) or DEFAULT_VIDEO_PATH.replace(".mp4", ".mkv"))
    summary["depth_keys"] = len(depth_keys)

    segments: list[tuple[int, str, int, float, float, Path]] = []
    for _, row in episodes.iterrows():
        new = int(row["episode_index"])
        length = int(row["length"])
        for key in video_tpl:
            try:
                rel = video_tpl[key].format(
                    video_key=key,
                    chunk_index=int(row[f"videos/{key}/chunk_index"]),
                    file_index=int(row[f"videos/{key}/file_index"]),
                )
                t0 = float(row[f"videos/{key}/from_timestamp"])
                t1 = float(row[f"videos/{key}/to_timestamp"])
            except (KeyError, TypeError, ValueError):
                problems.add(f"episode {new} {key}: video columns missing in meta/episodes")
                continue
            segments.append((new, key, length, t0, t1, out / rel))

    tol = 0.5 / fps
    files = sorted({seg[5] for seg in segments})
    decoded: dict[Path, tuple[np.ndarray, str] | str] = {}
    for done, path in enumerate(files, start=1):
        if on_progress:
            on_progress(done - 1, len(files))
        if not path.is_file():
            decoded[path] = "missing"
            continue
        try:
            decoded[path] = decode(path)
        except Exception as exc:
            decoded[path] = f"decode failed: {exc}"
    if on_progress:
        on_progress(len(files), len(files))

    depth_set = set(depth_keys)
    bad_segments = 0
    bad_depth_files: set[Path] = set()
    for new, key, length, t0, t1, path in segments:
        short = key.split(".")[-1]
        entry = decoded.get(path)
        if isinstance(entry, str):
            bad_segments += 1
            problems.add(f"episode {new} {short}: {entry} ({path.relative_to(out)})")
            continue
        stamps, pix_fmt = entry  # type: ignore[misc]
        if key in depth_set and not _SIXTEEN_BIT.search(pix_fmt) and path not in bad_depth_files:
            bad_depth_files.add(path)
            problems.add(f"depth {path.relative_to(out)} decodes as {pix_fmt or 'unknown'}, expected 16-bit")
        need = t0 + np.arange(length) / fps
        if len(stamps) == 0:
            worst = float("inf")
        else:
            pos = np.clip(np.searchsorted(stamps, need), 1, len(stamps) - 1) if len(stamps) > 1 else np.zeros(length, dtype=int)
            nearest = np.minimum(np.abs(stamps[pos] - need), np.abs(stamps[np.maximum(pos - 1, 0)] - need))
            worst = float(nearest.max()) if length else 0.0
        if worst > tol or abs((t1 - t0) - length / fps) > tol:
            bad_segments += 1
            problems.add(
                f"episode {new} {short}: {length} frames, segment {t1 - t0:.3f}s, worst timestamp gap {worst:.3f}s"
            )
    summary.update(video_files=len(files), video_segments=len(segments), bad_video_segments=bad_segments)
