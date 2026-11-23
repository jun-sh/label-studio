"""QC sidecar persistence — never writes into the source LeRobot dataset."""

from __future__ import annotations

import csv
import functools
import hashlib
import json
import os
import threading
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


def canonical_dataset_key(dataset_root: Path, *, resolve: bool = True) -> str:
    """Stable identity for a LeRobot package regardless of host mount prefix.

    resolve=False derives the key from the path text only (no lstat per component),
    for bulk use against slow FUSE/USB mounts.
    """
    path = dataset_root.resolve() if resolve else Path(os.path.normpath(str(dataset_root)))
    parts = list(path.parts)
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


def _locked(method):
    """Serialize access to the shared manifest (reviews, jobs) across request/rebuild threads."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


# One QcStore per sidecar directory in this process. Separate instances each hold their own
# in-memory manifest, and whichever saves last overwrites the other's changes (e.g. a
# rebuild's progress update wiping reviews made in a newer session).
_open_stores: dict[str, "QcStore"] = {}
_open_stores_lock = threading.Lock()


def forget_open_stores() -> None:
    """Drop cached stores (tests, or after editing sidecar files outside the service)."""
    with _open_stores_lock:
        _open_stores.clear()


@dataclass
class QcStore:
    sidecar_root: Path
    dataset_root: Path
    operator_id: str = "anonymous"
    manifest: dict[str, Any] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    @classmethod
    def open(cls, dataset_root: Path, sidecar_base: Path, operator_id: str = "anonymous") -> "QcStore":
        dataset_root = dataset_root.resolve()
        store_dir = resolve_sidecar_dir(dataset_root, sidecar_base)
        key = str(store_dir.resolve())
        with _open_stores_lock:
            existing = _open_stores.get(key)
            if existing is not None and existing.manifest_path.is_file():
                with existing._lock:
                    existing.operator_id = operator_id
                    if existing.dataset_root != dataset_root:
                        existing.dataset_root = dataset_root
                        existing.manifest["dataset_root"] = str(dataset_root)
                return existing
            store_dir.mkdir(parents=True, exist_ok=True)
            store = cls(sidecar_root=store_dir, dataset_root=dataset_root, operator_id=operator_id)
            store._load_manifest()
            _open_stores[key] = store
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

    @_locked
    def _save_manifest(self) -> None:
        self.manifest["updated_at"] = _utc_now()
        _atomic_write_text(
            self.manifest_path,
            json.dumps(self.manifest, indent=2, ensure_ascii=False),
        )

    def _resolve_operator_id(self, operator_id: str | None = None) -> str:
        raw = (operator_id or self.operator_id or "anonymous").strip()
        return raw or "anonymous"

    @_locked
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

    @_locked
    def get_review(self, episode_index: int) -> dict[str, Any]:
        reviews = self.manifest.get("reviews") or {}
        return dict(reviews.get(str(episode_index)) or {"status": "pending"})

    @_locked
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
        self.manifest["qc_updated_at"] = record["updated_at"]
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

    @_locked
    def get_instruction_override(self, episode_index: int) -> str | None:
        overrides = self.manifest.get("instruction_overrides") or {}
        value = overrides.get(str(episode_index))
        return str(value) if value is not None else None

    @_locked
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
        self.manifest["qc_updated_at"] = _utc_now()
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

    @_locked
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

    @_locked
    def visible_episode_indices(self, all_indices: list[int]) -> list[int]:
        removed = self.removed_episode_indices()
        return [idx for idx in all_indices if idx not in removed]

    @_locked
    def export_state(self) -> dict[str, Any]:
        return {
            "sidecar_root": str(self.sidecar_root),
            "dataset_root": str(self.dataset_root),
            "manifest": self.manifest,
            "removed_episode_indices": sorted(self.removed_episode_indices()),
            "audit_log_path": str(self.audit_path),
        }

    @_locked
    def register_rebuild_job(self, job: dict[str, Any]) -> None:
        jobs = self.manifest.setdefault("rebuild_jobs", [])
        jobs.append(job)
        self._save_manifest()
        self.append_audit(episode_index=None, action="rebuild_started", after=job)

    @_locked
    def update_rebuild_job(self, job_id: str, updates: dict[str, Any]) -> None:
        jobs = self.manifest.setdefault("rebuild_jobs", [])
        for job in jobs:
            if job.get("job_id") == job_id:
                job.update(updates)
                break
        self._save_manifest()

    @_locked
    def get_rebuild_job(self, job_id: str) -> dict[str, Any] | None:
        for job in self.manifest.get("rebuild_jobs") or []:
            if job.get("job_id") == job_id:
                return dict(job)
        return None

    @_locked
    def active_rebuild_job(self) -> dict[str, Any] | None:
        return manifest_active_rebuild_job(self.manifest)

    @_locked
    def last_qc_change_at(self) -> str | None:
        return manifest_last_qc_change_at(self.manifest)

    @_locked
    def delivery_status(self) -> dict[str, Any]:
        return manifest_delivery_status(self.manifest)

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

    @_locked
    def review_summary(
        self,
        all_episode_indices: list[int] | None = None,
        *,
        total_episodes: int | None = None,
    ) -> dict[str, Any]:
        total = total_episodes if total_episodes is not None else len(all_episode_indices or [])
        return {**manifest_review_counts(self.manifest, total), "delivery": self.delivery_status()}


def manifest_review_counts(manifest: dict[str, Any], total: int) -> dict[str, int]:
    """Review counts for a package with `total` episodes; unreviewed episodes count as pending."""
    counts = {"pending": 0, "approved": 0, "rejected": 0, "suspicious": 0}
    reviewed: set[int] = set()
    for key, record in (manifest.get("reviews") or {}).items():
        try:
            ep_idx = int(key)
        except (TypeError, ValueError):
            continue
        reviewed.add(ep_idx)
        status = record.get("status") or "pending"
        counts[status if status in counts else "pending"] += 1
    counts["pending"] += max(0, total - len(reviewed))
    visible = total - counts["rejected"]
    return {"total": total, "visible": visible, "removed_preview": total - visible, **counts}


def manifest_last_qc_change_at(manifest: dict[str, Any]) -> str | None:
    """Timestamp of the latest review/instruction change (ISO UTC, comparable as text)."""
    stamped = manifest.get("qc_updated_at")
    if stamped:
        return str(stamped)
    # Manifests written before qc_updated_at existed: fall back to review timestamps.
    times = [str(r.get("updated_at")) for r in (manifest.get("reviews") or {}).values() if r.get("updated_at")]
    return max(times) if times else None


def manifest_active_rebuild_job(manifest: dict[str, Any]) -> dict[str, Any] | None:
    for job in reversed(manifest.get("rebuild_jobs") or []):
        if job.get("status") in ("queued", "running"):
            return dict(job)
    return None


def manifest_delivery_status(manifest: dict[str, Any]) -> dict[str, Any]:
    """Whether the latest completed delivery reflects the current QC decisions.

    state: never_built | missing (output dir gone) | stale (QC changed after the job
    started, i.e. after it snapshotted the sidecar) | current.
    """
    completed = [
        job
        for job in manifest.get("rebuild_jobs") or []
        if job.get("status") == "completed" and job.get("output_root")
    ]
    last_change = manifest_last_qc_change_at(manifest)
    if not completed:
        return {"state": "never_built", "last_qc_change_at": last_change}
    last = max(completed, key=lambda job: str(job.get("finished_at") or job.get("created_at") or ""))
    snapshot_at = str(last.get("started_at") or last.get("created_at") or "")
    if not Path(str(last["output_root"])).is_dir():
        state = "missing"
    elif last_change and last_change > snapshot_at:
        state = "stale"
    else:
        state = "current"
    return {
        "state": state,
        "output_root": last.get("output_root"),
        "finished_at": last.get("finished_at"),
        "kept_episodes": last.get("kept_episodes"),
        "last_qc_change_at": last_change,
    }
