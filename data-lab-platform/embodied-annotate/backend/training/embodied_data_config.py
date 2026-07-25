"""Training-side data loading for embodied export bundles (E2).

Joins LeRobot frame parquet with meta/cycles.parquet and meta/episodes_meta.parquet
via episode_index. Training code MUST NOT read meta/lerobot_annotations.json.

See docs/export-contract.md for the frozen contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.dataset as ds


class EmbodiedDataConfig:
    """GR00T / RL-style dual-source config over an export bundle."""

    def __init__(self, dataset_root: str | Path) -> None:
        self.root = Path(dataset_root).expanduser().resolve()
        self.meta = self.root / "meta"
        self._assert_contract()

    def _assert_contract(self) -> None:
        manifest_path = self.meta / "export_manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"Missing {manifest_path}. Re-export with export contract v1.0+."
            )
        blocked = self.meta / "lerobot_annotations.json"
        if blocked.exists():
            raise ValueError(
                "Training bundle contains lerobot_annotations.json; "
                "re-export with a build that excludes annotation JSON."
            )

    @property
    def frames_dataset(self) -> ds.Dataset:
        return ds.dataset(str(self.root / "data"), format="parquet")

    @property
    def cycles_path(self) -> Path:
        return self.meta / "cycles.parquet"

    @property
    def episodes_meta_path(self) -> Path:
        return self.meta / "episodes_meta.parquet"

    def load_cycles(self, fail_reason: str | None = None) -> pd.DataFrame:
        if not self.cycles_path.is_file():
            return pd.DataFrame()
        if fail_reason is None:
            return pd.read_parquet(self.cycles_path)
        table = ds.dataset(self.cycles_path).to_table(
            filter=ds.field("fail_reason") == fail_reason,
        )
        return table.to_pandas()

    def load_episodes_meta(
        self,
        *,
        outcome: str | None = None,
        annotated_only: bool = False,
    ) -> pd.DataFrame:
        if not self.episodes_meta_path.is_file():
            return pd.DataFrame()
        df = pd.read_parquet(self.episodes_meta_path)
        if outcome is not None:
            df = df[df["outcome"] == outcome]
        if annotated_only:
            df = df[df["annotated"] == True]  # noqa: E712
        return df

    def episode_indices_for_fail_reason(self, fail_reason: str) -> list[int]:
        cycles = self.load_cycles(fail_reason=fail_reason)
        if cycles.empty:
            return []
        return sorted({int(x) for x in cycles["episode_index"].unique()})

    def filter_frame_episode_indices(self, episode_indices: Iterable[int]) -> ds.Dataset:
        """Return a PyArrow dataset over frames restricted to episode_index set."""
        allowed = sorted({int(x) for x in episode_indices})
        if not allowed:
            raise ValueError("episode_indices must be non-empty")
        return self.frames_dataset.filter(ds.field("episode_index").isin(allowed))

    def load_frames_for_fail_reason(self, fail_reason: str) -> pd.DataFrame:
        """Example: predicate pushdown on cycles, then join to frames."""
        episode_ids = self.episode_indices_for_fail_reason(fail_reason)
        if not episode_ids:
            return pd.DataFrame()
        filtered = self.filter_frame_episode_indices(episode_ids)
        return filtered.to_table().to_pandas()
