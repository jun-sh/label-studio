"""Import offline coarse screening results into QC sidecar state."""

from __future__ import annotations

from typing import Any

from qc_store import QcStore


def import_screening_payload(store: QcStore, payload: dict[str, Any]) -> dict[str, Any]:
    suspicious = payload.get("suspicious_episodes") or payload.get("episodes") or []
    auto_reject = payload.get("auto_reject_episodes") or payload.get("rejected_episodes") or []

    applied_suspicious: list[int] = []
    applied_rejected: list[int] = []
    skipped: list[dict[str, Any]] = []

    screening_meta = store.manifest.setdefault("coarse_screening", {})
    screening_meta["source"] = payload.get("source") or screening_meta.get("source") or "import"
    screening_meta["batch_id"] = payload.get("batch_id") or screening_meta.get("batch_id")

    for item in suspicious:
        ep_idx = _episode_index(item)
        if ep_idx is None:
            skipped.append({"item": item, "error": "missing episode_index"})
            continue
        current = store.get_review(ep_idx)
        if current.get("status") in ("approved", "rejected"):
            skipped.append({"episode_index": ep_idx, "error": f"already {current.get('status')}"})
            continue
        reason = _reason(item) or "coarse_screening_suspicious"
        store.set_review(ep_idx, "suspicious", reason=reason, note=_note(item))
        applied_suspicious.append(ep_idx)

    for item in auto_reject:
        ep_idx = _episode_index(item)
        if ep_idx is None:
            skipped.append({"item": item, "error": "missing episode_index"})
            continue
        reason = _reason(item) or "coarse_screening_auto_reject"
        store.set_review(ep_idx, "rejected", reason=reason, note=_note(item))
        applied_rejected.append(ep_idx)

    screening_meta["imported_suspicious"] = sorted(set(applied_suspicious))
    screening_meta["imported_rejected"] = sorted(set(applied_rejected))
    store._save_manifest()
    store.append_audit(
        episode_index=None,
        action="screening_import",
        after={
            "suspicious": applied_suspicious,
            "rejected": applied_rejected,
            "skipped": skipped,
        },
    )
    return {
        "suspicious": applied_suspicious,
        "rejected": applied_rejected,
        "skipped": skipped,
    }


def _episode_index(item: Any) -> int | None:
    if isinstance(item, int):
        return item
    if isinstance(item, dict):
        raw = item.get("episode_index", item.get("episode"))
        if raw is None:
            return None
        return int(raw)
    return None


def _reason(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    parts = []
    if item.get("reason"):
        parts.append(str(item["reason"]))
    flags = item.get("flags") or item.get("anomalies") or []
    if isinstance(flags, list) and flags:
        parts.extend(str(f) for f in flags)
    return "; ".join(parts) if parts else None


def _note(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("note") or item.get("comment") or "")
