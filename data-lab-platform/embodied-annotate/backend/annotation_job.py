"""Annotation Job model — pure task-family work orders (annotation_job_spec@1.0)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnnotationJob:
    """A single-family annotation work order within one physical package."""

    job_id: str
    display_name: str
    collection_id: str
    package_id: str
    task_family: str
    schema_id: str
    scope: str = "full"
    episode_indices: tuple[int, ...] = ()
    status: str = "open"
    priority: int = 100
    filters: dict[str, Any] | None = None
    sample: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnnotationJob:
        raw_indices = data.get("episode_indices") or []
        episode_indices = tuple(int(i) for i in raw_indices)
        return cls(
            job_id=str(data["job_id"]),
            display_name=str(data.get("display_name") or data["job_id"]),
            collection_id=str(data["collection_id"]),
            package_id=str(data["package_id"]),
            task_family=str(data["task_family"]),
            schema_id=str(data["schema_id"]),
            scope=str(data.get("scope") or "full"),
            episode_indices=episode_indices,
            status=str(data.get("status") or "open"),
            priority=int(data.get("priority") or 100),
            filters=data.get("filters") if isinstance(data.get("filters"), dict) else None,
            sample=data.get("sample") if isinstance(data.get("sample"), dict) else None,
        )

    def episode_allowed(self, episode_index: int) -> bool:
        if self.scope == "full":
            return True
        if not self.episode_indices:
            return False
        return int(episode_index) in self.episode_indices
