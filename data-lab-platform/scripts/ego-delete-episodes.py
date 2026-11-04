#!/usr/bin/env python3
"""Delete selected LeRobot episodes from a 34 stream station (collection L2 view).

Preserves raw/segments on disk. Removes L2 data/videos shards and episode metadata.
Does not compact episode_index (gaps are OK for the viewer).

Usage:
  ego-delete-episodes.py ego-002 --index 12,31,34
  ego-delete-episodes.py ego-002 --index 12,31 --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLATFORM_ROOT = SCRIPT_DIR.parent
_sync_candidates = (
    SCRIPT_DIR / "sync-stream-parquet.py",
    PLATFORM_ROOT / "lerobot-studio" / "scripts" / "sync-stream-parquet.py",
)
SYNC_SCRIPT = next((p for p in _sync_candidates if p.is_file()), _sync_candidates[0])
STATION_RE = re.compile(r"^ego-[a-z0-9-]+$")


def episode_chunk_paths(episode_index: int) -> dict[str, str]:
    chunk_index = episode_index // 1000
    file_index = episode_index % 1000
    c = f"{chunk_index:03d}"
    f = f"{file_index:03d}"
    return {
        "data_parquet": f"data/chunk-{c}/file-{f}.parquet",
        "data_jsonl": f"data/chunk-{c}/file-{f}.jsonl",
        "video_tpl": f"videos/{{video_key}}/chunk-{c}/file-{f}.mp4",
    }


def read_json(path: Path, default: object) -> object:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def video_keys(info: dict) -> list[str]:
    features = info.get("features") if isinstance(info, dict) else {}
    if not isinstance(features, dict):
        return []
    return [k for k, spec in features.items() if isinstance(spec, dict) and spec.get("dtype") == "video"]


def load_catalog(root: Path) -> tuple[str, list[dict], dict | None]:
    manifest = read_json(root / "manifest" / "manifest.json", {})
    episodes = manifest.get("episodes") if isinstance(manifest, dict) else None
    if isinstance(episodes, list) and episodes:
        return "manifest", list(episodes), manifest

    index = read_json(root / "live" / "episodes-index.json", {})
    eps = index.get("episodes") if isinstance(index, dict) else None
    if isinstance(eps, list) and eps:
        return "index", list(eps), index

    parquet_ep = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    if parquet_ep.is_file():
        try:
            import pyarrow.parquet as pq

            table = pq.read_table(parquet_ep)
            if "episode_index" in table.column_names:
                rows = []
                for i in range(table.num_rows):
                    rows.append(
                        {
                            "episode_index": int(table["episode_index"][i].as_py()),
                            "session_id": "",
                            "frames": int(table["length"][i].as_py())
                            if "length" in table.column_names
                            else 0,
                        }
                    )
                if rows:
                    return "parquet", rows, None
        except Exception:
            pass

    return "none", [], None


def l2_paths(root: Path, episode_index: int, info: dict) -> list[Path]:
    rels = episode_chunk_paths(episode_index)
    paths = [
        root / rels["data_parquet"],
        root / rels["data_jsonl"],
    ]
    for key in video_keys(info):
        rel = rels["video_tpl"].format(video_key=key)
        paths.append(root / rel)
    sensor = root / "sensor_raw" / f"episode_{episode_index:06d}.parquet"
    paths.append(sensor)
    return [p for p in paths if p.is_file()]


def audit(root: Path, station: str, indices: list[int], apply: bool) -> None:
    log_path = Path(os.environ.get("EGO_OPS_AUDIT_LOG", root.parent.parent / "audit" / "ego-ops.jsonl"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": "delete_episodes_apply" if apply else "delete_episodes_dry_run",
        "station": station,
        "episode_indices": indices,
        "user": os.environ.get("USER", "unknown"),
        "host": os.uname().nodename,
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def sync_meta(root: Path, has_manifest: bool) -> None:
    if not SYNC_SCRIPT.is_file():
        print(f"[delete-episodes] WARN: missing {SYNC_SCRIPT}", file=sys.stderr)
        return
    subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--meta-only", str(root)],
        check=False,
    )
    if has_manifest:
        subprocess.run(
            [sys.executable, str(SYNC_SCRIPT), "--republish-units", str(root)],
            check=False,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("station", help="e.g. ego-002")
    parser.add_argument(
        "--index",
        required=True,
        help="Comma-separated episode_index values (sidebar # N)",
    )
    parser.add_argument("--apply", action="store_true", help="Execute deletes")
    parser.add_argument("--json", action="store_true", help="Emit one JSON line on stdout (for API)")
    args = parser.parse_args()

    station = args.station.strip()
    if not STATION_RE.match(station):
        print(f"invalid station id: {station}", file=sys.stderr)
        return 2

    indices: list[int] = []
    for part in str(args.index).split(","):
        part = part.strip()
        if not part:
            continue
        indices.append(int(part))
    indices = sorted(set(indices))
    if not indices:
        print("no episode indices", file=sys.stderr)
        return 2

    default_stream = PLATFORM_ROOT / "data-storage" / "stream"
    stream_root = Path(
        os.environ.get("EGO_STREAM_ROOT")
        or os.environ.get("STREAM_DATA_ROOT")
        or (default_stream if default_stream.is_dir() else Path("/srv/stream"))
    )
    root = stream_root / station
    if not root.is_dir():
        print(f"station path missing: {root}", file=sys.stderr)
        return 1

    info = read_json(root / "meta" / "info.json", {})
    source, episodes, container = load_catalog(root)
    if not episodes:
        print(f"no episodes catalog in {root} (manifest or live/episodes-index.json)", file=sys.stderr)
        return 1

    by_index = {int(ep.get("episode_index", -1)): ep for ep in episodes}
    missing = [i for i in indices if i not in by_index]
    if missing:
        print(f"episode_index not in catalog: {missing}", file=sys.stderr)
        return 1

    actions: list[str] = []
    derived_dirs: list[Path] = []

    for idx in indices:
        ep = by_index[idx]
        session_id = str(ep.get("session_id") or "").strip()
        for path in l2_paths(root, idx, info if isinstance(info, dict) else {}):
            actions.append(f"unlink {path.relative_to(root)}")
        if session_id:
            unit = root / "derived" / session_id
            if unit.is_dir():
                derived_dirs.append(unit)
                actions.append(f"rmtree derived/{session_id}")

    actions.append(f"remove episode_index from {source} catalog: {indices}")
    actions.append("sync-stream-parquet.py --meta-only" + (" + --republish-units" if source == "manifest" else ""))

    print(f"[delete-episodes] station={station} dry_run={not args.apply}")
    for line in actions:
        print(f"  {line}")

    audit(root, station, indices, args.apply)
    if not args.apply:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "station": station,
                        "episode_indices": indices,
                        "actions": actions,
                    }
                )
            )
        else:
            print("[delete-episodes] re-run with --apply to execute")
        return 0

    delete_set = set(indices)
    for idx in indices:
        for path in l2_paths(root, idx, info if isinstance(info, dict) else {}):
            path.unlink(missing_ok=True)

    for unit in derived_dirs:
        shutil.rmtree(unit, ignore_errors=True)

    if source == "manifest" and isinstance(container, dict):
        kept = [ep for ep in episodes if int(ep.get("episode_index", -1)) not in delete_set]
        container["episodes"] = kept
        total_frames = sum(int(ep.get("frames") or 0) for ep in kept)
        container["total_frames"] = total_frames
        write_json(root / "manifest" / "manifest.json", container)
    elif source == "index" and isinstance(container, dict):
        kept = [ep for ep in episodes if int(ep.get("episode_index", -1)) not in delete_set]
        container["episodes"] = kept
        write_json(root / "live" / "episodes-index.json", container)
    elif source == "parquet":
        kept = [ep for ep in episodes if int(ep.get("episode_index", -1)) not in delete_set]
        index_doc = read_json(root / "live" / "episodes-index.json", {})
        if not isinstance(index_doc, dict):
            index_doc = {"version": 1}
        index_doc["episodes"] = kept
        write_json(root / "live" / "episodes-index.json", index_doc)

    sync_meta(root, source == "manifest")
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": False,
                    "station": station,
                    "removed": indices,
                    "raw_preserved": True,
                }
            )
        )
    else:
        print(f"[delete-episodes] done removed={indices} raw/segments untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
