from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TimeDomain = Literal["monotonic_ns", "utc_ns", "frame_index"]


@dataclass
class ClockModel:
    """Documents how streams were synchronized; extend with hardware offsets in production."""

    master_domain: TimeDomain | str = "monotonic_ns"
    av_timebase: str = "best_effort_pts"
    remarks: str | None = None
