from __future__ import annotations

import json
from pathlib import Path

from ego_capture_studio.schema.episode import LanguageSegment


class JsonFileLanguageProvider:
    def __init__(self, relative_name: str = "language.json") -> None:
        self.relative_name = relative_name

    def load(self, episode_root: Path) -> list[LanguageSegment]:
        path = episode_root / self.relative_name
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        segs = data.get("segments", [])
        out: list[LanguageSegment] = []
        for s in segs:
            out.append(
                LanguageSegment(
                    t_start_ns=int(s["t_start_ns"]),
                    t_end_ns=int(s["t_end_ns"]),
                    text=str(s["text"]),
                    speaker=s.get("speaker"),
                )
            )
        return out
