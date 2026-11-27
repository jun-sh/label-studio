"""Daily QC activity across all sidecars: episodes reviewed and deliveries finished per day.

Read-only. Used by the Datalab overview page for its trend charts.

- Reviews are bucketed by each episode's latest review time (``updated_at``); the manifest
  keeps only the current review per episode, so an episode re-reviewed later moves to the
  later day. Episodes reset to "pending" are not counted.
- Deliveries are completed rebuild jobs, bucketed by ``finished_at``, with the number of
  episodes kept in the delivered dataset.
- Days are calendar days in the caller's timezone (``tz_offset_minutes`` east of UTC).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from overview_service import index_sidecars

MAX_DAYS = 180
REVIEW_STATUSES = ("approved", "rejected", "suspicious")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _jobs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = manifest.get("rebuild_jobs") or []
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    return [job for job in jobs if isinstance(job, dict)]


def build_daily_trends(
    sidecar_base: Path,
    days: int = 30,
    tz_offset_minutes: int = 0,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    days = max(1, min(MAX_DAYS, int(days)))
    tz = timezone(timedelta(minutes=int(tz_offset_minutes)))
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    start = today - timedelta(days=days - 1)

    buckets: dict[date, dict[str, Any]] = {}
    for offset in range(days):
        day = start + timedelta(days=offset)
        buckets[day] = {
            "date": day.isoformat(),
            "reviewed": 0,
            **{status: 0 for status in REVIEW_STATUSES},
            "deliveries": 0,
            "delivered_episodes": 0,
        }

    def bucket(value: Any) -> dict[str, Any] | None:
        moment = _parse_time(value)
        return buckets.get(moment.astimezone(tz).date()) if moment else None

    for manifest in index_sidecars(sidecar_base).values():
        for review in (manifest.get("reviews") or {}).values():
            if not isinstance(review, dict) or review.get("status") not in REVIEW_STATUSES:
                continue
            row = bucket(review.get("updated_at"))
            if row is not None:
                row["reviewed"] += 1
                row[review["status"]] += 1
        for job in _jobs(manifest):
            if job.get("status") != "completed":
                continue
            row = bucket(job.get("finished_at"))
            if row is not None:
                row["deliveries"] += 1
                row["delivered_episodes"] += int(job.get("kept_episodes") or 0)

    series = [buckets[day] for day in sorted(buckets)]
    totals = {
        key: sum(row[key] for row in series)
        for key in ("reviewed", *REVIEW_STATUSES, "deliveries", "delivered_episodes")
    }
    return {
        "days": days,
        "tz_offset_minutes": int(tz_offset_minutes),
        "start": start.isoformat(),
        "end": today.isoformat(),
        "series": series,
        "totals": totals,
    }
