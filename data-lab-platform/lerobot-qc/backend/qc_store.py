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


def dataset_sidecar_id(dataset_root: Path) -> str:
    digest = hashlib.sha256(str(dataset_root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"{dataset_root.name}_{digest}"


@dataclass
class QcStore:
    sidecar_root: Path
    dataset_root: Path
    operator_id: str = "anonymous"
    manifest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def open(cls, dataset_root: Path, sidecar_base: Path, operator_id: str = "anonymous") -> "QcStore":
        dataset_root = dataset_root.resolve()
        store_dir = sidecar_base / dataset_sidecar_id(dataset_root)
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
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    def append_audit(
        self,
        *,
        episode_index: int | None,
        action: str,
        before: Any = None,
        after: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "timestamp": _utc_now(),
            "operator_id": self.operator_id,
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
    ) -> dict[str, Any]:
        reviews = self.manifest.setdefault("reviews", {})
        key = str(episode_index)
        previous = dict(reviews.get(key) or {})
        record = {
            "status": status,
            "reason": reason or "",
            "note": note or "",
            "updated_at": _utc_now(),
            "operator_id": self.operator_id,
        }
        reviews[key] = record
        self._save_manifest()
        self._sync_removed_json()
        self.append_audit(
            episode_index=episode_index,
            action=f"review_{status}",
            before=previous,
            after=record,
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
    ) -> dict[str, Any]:
        overrides = self.manifest.setdefault("instruction_overrides", {})
        key = str(episode_index)
        previous = overrides.get(key)
        overrides[key] = new_instruction
        self._save_manifest()
        self._append_correction_row(
            episode_index=episode_index,
            original=original_instruction,
            corrected=new_instruction,
        )
        self.append_audit(
            episode_index=episode_index,
            action="instruction_edit",
            before={"instruction": previous or original_instruction},
            after={"instruction": new_instruction},
        )
        return {"episode_index": episode_index, "instruction": new_instruction}

    def _append_correction_row(self, episode_index: int, original: str, corrected: str) -> None:
        write_header = not self.corrections_path.is_file()
        with self.corrections_path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            if write_header:
                writer.writerow(
                    ["timestamp", "operator_id", "episode_index", "original_instruction", "corrected_instruction"]
                )
            writer.writerow([_utc_now(), self.operator_id, episode_index, original, corrected])

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
        self.removed_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

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
