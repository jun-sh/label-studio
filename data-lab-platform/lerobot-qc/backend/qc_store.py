"""QC sidecar persistence — never writes into the source LeRobot dataset."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

ReviewStatus = Literal["pending", "approved", "rejected", "suspicious"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def canonical_dataset_key(dataset_root: Path) -> str:
    """Stable identity for a LeRobot package regardless of host mount prefix."""
    parts = list(dataset_root.resolve().parts)
    start = 0
    for index, part in enumerate(parts):
        lowered = part.lower()
        if lowered in ("bookduo", "host-media") or part.startswith("BookDuo"):
            start = index + 1
    tail = parts[start:]
    if len(tail) >= 2:
        return f"{tail[-2]}/{tail[-1]}"
    if tail:
        return tail[-1]
    return dataset_root.name


def dataset_sidecar_id(dataset_root: Path) -> str:
    key = canonical_dataset_key(dataset_root)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{dataset_root.name}_{digest}"


def legacy_dataset_sidecar_id(dataset_root: Path) -> str:
    digest = hashlib.sha256(str(dataset_root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"{dataset_root.name}_{digest}"


def _manifest_review_count(manifest: dict[str, Any]) -> int:
    return len(manifest.get("reviews") or {})


def resolve_sidecar_dir(dataset_root: Path, sidecar_base: Path) -> Path:
    """Find the best existing sidecar directory for a dataset package."""
    dataset_key = canonical_dataset_key(dataset_root)
    canonical_dir = sidecar_base / dataset_sidecar_id(dataset_root)
    legacy_dir = sidecar_base / legacy_dataset_sidecar_id(dataset_root)

    candidates: dict[Path, int] = {}

    def consider(path: Path) -> None:
        if not path.is_dir():
            return
        manifest_path = path / "qc_manifest.json"
        if not manifest_path.is_file():
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        stored_root = manifest.get("dataset_root")
        if stored_root and canonical_dataset_key(Path(str(stored_root))) == dataset_key:
            candidates[path] = max(candidates.get(path, 0), _manifest_review_count(manifest))

    consider(canonical_dir)
    consider(legacy_dir)
    if sidecar_base.is_dir():
        for entry in sidecar_base.iterdir():
            consider(entry)

    if candidates:
        return max(candidates.items(), key=lambda item: (item[1], str(item[0])))[0]
    return canonical_dir


@dataclass
class QcStore:
    sidecar_root: Path
    dataset_root: Path
    operator_id: str = "anonymous"
    manifest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def open(cls, dataset_root: Path, sidecar_base: Path, operator_id: str = "anonymous") -> "QcStore":
        dataset_root = dataset_root.resolve()
        store_dir = resolve_sidecar_dir(dataset_root, sidecar_base)
        store_dir.mkdir(parents=True, exist_ok=True)
        store = cls(sidecar_root=store_dir, dataset_root=dataset_root, operator_id=operator_id)
        store._load_manifest()
        return store

    @property
    def manifest_path(self) -> Path:
        return self.sidecar_root / "qc_manifest.json"

    @property
    def removed_path(self) -> Path:
        return self.sidecar_root / "removed_episodes.json"

    @property
    def corrections_path(self) -> Path:
        return self.sidecar_root / "annotation_corrections.csv"

    @property
    def audit_path(self) -> Path:
        return self.sidecar_root / "audit_log.jsonl"

    def _load_manifest(self) -> None:
        if self.manifest_path.is_file():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            self.manifest["dataset_root"] = str(self.dataset_root)
            return
        self.manifest = {
            "version": 1,
            "dataset_root": str(self.dataset_root),
            "dataset_readonly": True,
            "created_at": _utc_now(),
            "reviews": {},
            "instruction_overrides": {},
            "rebuild_jobs": [],
        }
        self._save_manifest()

    def _save_manifest(self) -> None:
        self.manifest["updated_at"] = _utc_now()
        _atomic_write_text(
            self.manifest_path,
            json.dumps(self.manifest, indent=2, ensure_ascii=False),
        )

    def _resolve_operator_id(self, operator_id: str | None = None) -> str:
        raw = (operator_id or self.operator_id or "anonymous").strip()
        return raw or "anonymous"

    def append_audit(
        self,
        *,
        episode_index: int | None,
        action: str,
        before: Any = None,
        after: Any = None,
        extra: dict[str, Any] | None = None,
        operator_id: str | None = None,
    ) -> None:
        entry = {
            "timestamp": _utc_now(),
            "operator_id": self._resolve_operator_id(operator_id),
            "dataset_root": str(self.dataset_root),
            "episode_index": episode_index,
            "action": action,
            "before": before,
            "after": after,
        }
        if extra:
            entry.update(extra)
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_review(self, episode_index: int) -> dict[str, Any]:
        reviews = self.manifest.get("reviews") or {}
        return dict(reviews.get(str(episode_index)) or {"status": "pending"})

    def set_review(
        self,
        episode_index: int,
        status: ReviewStatus,
        *,
        reason: str | None = None,
        note: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        reviews = self.manifest.setdefault("reviews", {})
        key = str(episode_index)
        previous = dict(reviews.get(key) or {})
        resolved_operator = self._resolve_operator_id(operator_id)
        record = {
            "status": status,
            "reason": reason or "",
            "note": note or "",
            "updated_at": _utc_now(),
            "operator_id": resolved_operator,
        }
        reviews[key] = record
        self._save_manifest()
        self._sync_removed_json()
        self.append_audit(
            episode_index=episode_index,
            action=f"review_{status}",
            before=previous,
            after=record,
            operator_id=resolved_operator,
        )
        return record

    def get_instruction_override(self, episode_index: int) -> str | None:
        overrides = self.manifest.get("instruction_overrides") or {}
        value = overrides.get(str(episode_index))
        return str(value) if value is not None else None

    def set_instruction_override(
        self,
        episode_index: int,
        new_instruction: str,
        *,
        original_instruction: str,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        overrides = self.manifest.setdefault("instruction_overrides", {})
        key = str(episode_index)
        previous = overrides.get(key)
        resolved_operator = self._resolve_operator_id(operator_id)
        overrides[key] = new_instruction
        self._save_manifest()
        self._append_correction_row(
            episode_index=episode_index,
            original=original_instruction,
            corrected=new_instruction,
            operator_id=resolved_operator,
        )
        self.append_audit(
            episode_index=episode_index,
            action="instruction_edit",
            before={"instruction": previous or original_instruction},
            after={"instruction": new_instruction},
            operator_id=resolved_operator,
        )
        return {"episode_index": episode_index, "instruction": new_instruction}

    def _append_correction_row(
        self,
        episode_index: int,
        original: str,
        corrected: str,
        *,
        operator_id: str | None = None,
    ) -> None:
        write_header = not self.corrections_path.is_file()
        with self.corrections_path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            if write_header:
                writer.writerow(
                    ["timestamp", "operator_id", "episode_index", "original_instruction", "corrected_instruction"]
                )
            writer.writerow(
                [_utc_now(), self._resolve_operator_id(operator_id), episode_index, original, corrected]
            )

    def removed_episode_indices(self) -> set[int]:
        removed: set[int] = set()
        reviews = self.manifest.get("reviews") or {}
        for key, record in reviews.items():
            if record.get("status") == "rejected":
                removed.add(int(key))
        return removed

    def _sync_removed_json(self) -> None:
        payload = {
            "dataset_root": str(self.dataset_root),
            "updated_at": _utc_now(),
            "removed_episodes": [],
        }
        reviews = self.manifest.get("reviews") or {}
        for key, record in sorted(reviews.items(), key=lambda item: int(item[0])):
            if record.get("status") != "rejected":
                continue
            payload["removed_episodes"].append(
                {
                    "episode_index": int(key),
                    "reason": record.get("reason") or "",
                    "note": record.get("note") or "",
                    "operator_id": record.get("operator_id") or "",
                    "updated_at": record.get("updated_at") or "",
                }
            )
        _atomic_write_text(
            self.removed_path,
            json.dumps(payload, indent=2, ensure_ascii=False),
        )

    def visible_episode_indices(self, all_indices: list[int]) -> list[int]:
        removed = self.removed_episode_indices()
        return [idx for idx in all_indices if idx not in removed]

    def export_state(self) -> dict[str, Any]:
        return {
            "sidecar_root": str(self.sidecar_root),
            "dataset_root": str(self.dataset_root),
            "manifest": self.manifest,
            "removed_episode_indices": sorted(self.removed_episode_indices()),
            "audit_log_path": str(self.audit_path),
        }

    def register_rebuild_job(self, job: dict[str, Any]) -> None:
        jobs = self.manifest.setdefault("rebuild_jobs", [])
        jobs.append(job)
        self._save_manifest()
        self.append_audit(episode_index=None, action="rebuild_started", after=job)

    def update_rebuild_job(self, job_id: str, updates: dict[str, Any]) -> None:
        jobs = self.manifest.setdefault("rebuild_jobs", [])
        for job in jobs:
            if job.get("job_id") == job_id:
                job.update(updates)
                break
        self._save_manifest()

    def get_rebuild_job(self, job_id: str) -> dict[str, Any] | None:
        for job in self.manifest.get("rebuild_jobs") or []:
            if job.get("job_id") == job_id:
                return dict(job)
        return None

    def active_rebuild_job(self) -> dict[str, Any] | None:
        for job in reversed(self.manifest.get("rebuild_jobs") or []):
            if job.get("status") in ("queued", "running"):
                return dict(job)
        return None

    def read_audit_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.audit_path.is_file():
            return []
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
        return entries

    def review_summary(
        self,
        all_episode_indices: list[int] | None = None,
        *,
        total_episodes: int | None = None,
    ) -> dict[str, Any]:
        total = total_episodes if total_episodes is not None else len(all_episode_indices or [])
        counts = {"pending": 0, "approved": 0, "rejected": 0, "suspicious": 0}
        reviews = self.manifest.get("reviews") or {}
        reviewed_indices: set[int] = set()
        for key, record in reviews.items():
            try:
                ep_idx = int(key)
            except (TypeError, ValueError):
                continue
            reviewed_indices.add(ep_idx)
            status = record.get("status") or "pending"
            if status not in counts:
                status = "pending"
            counts[status] += 1
        counts["pending"] += max(0, total - len(reviewed_indices))
        visible = total - len(self.removed_episode_indices())
        return {
            "total": total,
            "visible": visible,
            "removed_preview": total - visible,
            **counts,
        }
