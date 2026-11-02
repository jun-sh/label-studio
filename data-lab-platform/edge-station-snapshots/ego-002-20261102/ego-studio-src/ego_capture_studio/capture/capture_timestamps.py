"""Resolve commit timestamps: device time (default) or legacy synthetic grid."""

from __future__ import annotations

import os
from typing import Callable


def synthetic_grid_enabled() -> bool:
    """Legacy +33ms grid; default off (EGO_CAPTURE_SYNTHETIC_GRID=0)."""
    return os.environ.get("EGO_CAPTURE_SYNTHETIC_GRID", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def resolve_commit_timestamp_ns(
    primary_dev_ns: int,
    *,
    last_emit_ns: int | None,
    interval_ns: int,
    grid_epoch_ns: int = 0,
    align_epoch_to_device: Callable[[int, int], int] | None = None,
) -> int:
    """Return MCAP commit timestamp for this frame."""
    dev_ns = int(primary_dev_ns)
    if not synthetic_grid_enabled():
        return dev_ns

    if last_emit_ns is None:
        if int(grid_epoch_ns) > 0:
            return int(grid_epoch_ns)
        if align_epoch_to_device is not None:
            return int(align_epoch_to_device(dev_ns, int(interval_ns)))
        return dev_ns
    return int(last_emit_ns) + int(interval_ns)
