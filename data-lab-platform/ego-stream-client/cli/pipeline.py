from __future__ import annotations

import argparse
from pathlib import Path

from ego_capture_studio.pipeline.orchestrator import PipelineConfig, run_episode_pipeline
from ego_capture_studio.schema.episode import EpisodeManifest


def load_or_create_manifest(episode_root: Path, episode_id: str | None) -> EpisodeManifest:
    episode_root = episode_root.resolve()
    path = episode_root / "manifest.json"
    eid = episode_id or episode_root.name
    if path.is_file():
        return EpisodeManifest.model_validate_json(path.read_text(encoding="utf-8"))
    return EpisodeManifest(episode_id=eid)


def main() -> None:
    p = argparse.ArgumentParser(description="Run ego capture pipeline on one episode directory.")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--episode-root", type=Path, required=True)
    p.add_argument("--episode-id", default=None, help="Ignored if manifest.json exists.")
    args = p.parse_args()

    cfg = PipelineConfig.from_yaml(args.config)
    manifest = load_or_create_manifest(args.episode_root, args.episode_id)
    out = run_episode_pipeline(args.episode_root, cfg, manifest)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
