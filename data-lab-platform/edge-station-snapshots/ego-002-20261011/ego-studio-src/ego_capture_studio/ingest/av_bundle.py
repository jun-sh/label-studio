from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AVBundle:
    """Expected media layout under an episode root."""

    root: Path
    video: Path | None = None
    video_left: Path | None = None
    video_right: Path | None = None
    audio: Path | None = None

    @classmethod
    def discover(cls, root: Path) -> AVBundle:
        root = root.resolve()
        v = root / "video.mp4"
        vl = root / "video_left.mp4"
        vr = root / "video_right.mp4"
        a = root / "audio.wav"
        return cls(
            root=root,
            video=v if v.is_file() else None,
            video_left=vl if vl.is_file() else None,
            video_right=vr if vr.is_file() else None,
            audio=a if a.is_file() else None,
        )
