#!/usr/bin/env python3
"""
Offline export: pack closed pending segments to ready/YYYYMMDD/, mark uploaded, delete source.

Run via ego-studio venv (see scripts/export-offline.sh). Uses segment_store state machine
and pack_segment_tar_zst — same primitives as network upload, without HTTP.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from ego_capture_studio.capture.segment_store import (
    list_closed_pending_segments,
    mark_segment_uploaded,
)
from ego_capture_studio.capture.segment_tar_zst import pack_segment_tar_zst, segment_archive_basename, sha256_file

CAPTURE_TARGET = os.environ.get("EGO_CAPTURE_TARGET", "ecs-oak-capture-stack.target")
DELETE_AFTER = os.environ.get("EGO_EXPORT_DELETE_AFTER", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)


def _capture_active() -> bool:
    proc = subprocess.run(
        ["systemctl", "--user", "is-active", CAPTURE_TARGET],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return proc.stdout.strip() == "active"


def _list_all_pending(segment_root: Path) -> list[Path]:
    sessions_dir = segment_root / "sessions"
    if not sessions_dir.is_dir():
        return []
    pending: list[Path] = []
    seen: set[str] = set()
    for sess in sorted(sessions_dir.iterdir()):
        if not sess.is_dir():
            continue
        for seg in list_closed_pending_segments(segment_root, sess.name):
            key = str(seg.resolve())
            if key not in seen:
                seen.add(key)
                pending.append(seg)
    return sorted(pending, key=lambda p: (p.parent.parent.name, p.name))


def _append_ledger(ledger_path: Path, record: dict[str, object]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def _export_one(
    segment_dir: Path,
    *,
    ready_dir: Path,
    staging_dir: Path,
    ledger_path: Path,
    dry_run: bool,
) -> tuple[bool, str]:
    segment_id = segment_dir.name
    manifest_path = segment_dir / "manifest.json"
    session_id = ""

    if not segment_dir.is_dir():
        candidates = sorted(ready_dir.glob(f"sess_*__{segment_id}.tar.zst"))
        final_guess = candidates[0] if candidates else ready_dir / f"{segment_id}.tar.zst"
        if final_guess.is_file():
            digest = sha256_file(final_guess)
            _append_ledger(
                ledger_path,
                {
                    "segment_id": segment_id,
                    "session_id": session_id,
                    "archive": str(final_guess),
                    "sha256": digest,
                    "exported_at": _utc_now(),
                    "note": "ready_present_source_gone",
                },
            )
            return True, f"{segment_id}: ready file exists (source already removed)"
        return True, f"{segment_id}: skipped stale (directory missing)"

    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"{segment_id}: unreadable manifest ({exc})"
        segment_id = str(manifest.get("segment_id") or segment_id)
        session_id = str(manifest.get("session_id") or "")

    final_name = segment_archive_basename(session_id, segment_id) if session_id else f"{segment_id}.tar.zst"
    final_path = ready_dir / final_name
    staging_path = staging_dir / f"{final_name}.part"

    if final_path.is_file():
        digest = sha256_file(final_path)
        if not dry_run and manifest_path.is_file():
            mark_segment_uploaded(segment_dir, delete=DELETE_AFTER)
        note = "already_present"
        if not manifest_path.is_file():
            note = "ready_present_source_gone"
        _append_ledger(
            ledger_path,
            {
                "segment_id": segment_id,
                "session_id": session_id,
                "archive": str(final_path),
                "sha256": digest,
                "exported_at": _utc_now(),
                "note": note,
            },
        )
        if manifest_path.is_file():
            return True, f"{segment_id}: ready file exists, marked uploaded"
        return True, f"{segment_id}: ready file exists (source already removed)"

    if not manifest_path.is_file():
        return True, f"{segment_id}: skipped stale (source missing, no ready archive)"

    if dry_run:
        return True, f"{segment_id}: dry-run ok"

    staging_dir.mkdir(parents=True, exist_ok=True)
    ready_dir.mkdir(parents=True, exist_ok=True)
    if staging_path.is_file():
        staging_path.unlink()

    try:
        _, digest_pack = pack_segment_tar_zst(segment_dir, staging_path)
        digest_verify = sha256_file(staging_path)
        if digest_verify != digest_pack:
            staging_path.unlink(missing_ok=True)
            return False, f"{segment_id}: sha256 mismatch after pack"
        os.replace(staging_path, final_path)
        digest_final = sha256_file(final_path)
        if digest_final != digest_pack:
            final_path.unlink(missing_ok=True)
            return False, f"{segment_id}: sha256 mismatch after move to ready"
        mark_segment_uploaded(segment_dir, delete=DELETE_AFTER)
        _append_ledger(
            ledger_path,
            {
                "segment_id": segment_id,
                "session_id": session_id,
                "archive": str(final_path),
                "sha256": digest_final,
                "exported_at": _utc_now(),
            },
        )
        return True, f"{segment_id}: exported -> {final_path}"
    except Exception as exc:
        staging_path.unlink(missing_ok=True)
        return False, f"{segment_id}: {exc}"


def _reset_export_state(segment_root: Path, export_root: Path) -> tuple[int, int]:
    """Clear export artifacts and mark closed segments as not uploaded (idempotent re-export)."""
    cleared_dirs = 0
    for sub in ("staging", "ready"):
        target = export_root / sub
        if target.is_dir():
            shutil.rmtree(target)
            cleared_dirs += 1

    reset_segments = 0
    sessions_dir = segment_root / "sessions"
    if sessions_dir.is_dir():
        for manifest_path in sessions_dir.rglob("manifest.json"):
            seg_dir = manifest_path.parent
            if not seg_dir.name.startswith("seg_"):
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not manifest.get("closed"):
                continue
            if manifest.get("uploaded"):
                manifest["uploaded"] = False
                manifest.pop("uploadedAt", None)
                manifest_path.write_text(
                    json.dumps(manifest, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                reset_segments += 1
    return cleared_dirs, reset_segments


def _segment_bytes_before(segment_root: Path) -> int:
    total = 0
    sessions_dir = segment_root / "sessions"
    if not sessions_dir.is_dir():
        return 0
    for path in sessions_dir.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export closed pending segments to ready/YYYYMMDD/ (offline).",
    )
    parser.add_argument(
        "--segment-root",
        type=Path,
        default=Path(os.environ.get("EGO_SEGMENT_ROOT", "/home/server/cache/ego-lan-214/segments")),
    )
    parser.add_argument(
        "--export-root",
        type=Path,
        default=Path(os.environ.get("EGO_EXPORT_ROOT", "/home/server/export/ego-lan-214")),
    )
    parser.add_argument(
        "--date",
        type=str,
        default=os.environ.get("EGO_EXPORT_DATE", ""),
        help="YYYYMMDD subdir under ready/ (default: today)",
    )
    parser.add_argument(
        "--skip-capture-check",
        action="store_true",
        help="Allow export while capture stack is active (not recommended)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear export/ ready+staging and mark closed segments uploaded=false before export",
    )
    args = parser.parse_args()

    segment_root = args.segment_root.resolve()
    export_root = args.export_root.resolve()
    day = args.date.strip() or datetime.now().strftime("%Y%m%d")
    ready_dir = export_root / "ready" / day
    staging_dir = export_root / "staging"
    ledger_path = ready_dir / "export-manifest.jsonl"

    if not args.skip_capture_check and _capture_active():
        print("ERROR: capture stack is active; stop recording before export.", file=sys.stderr)
        print(f"  systemctl --user stop {CAPTURE_TARGET}", file=sys.stderr)
        return 2

    bytes_before = _segment_bytes_before(segment_root)

    if args.reset:
        cleared_dirs, reset_segments = _reset_export_state(segment_root, export_root)
        print(
            f"reset cleared_export_dirs={cleared_dirs} reset_uploaded_segments={reset_segments}",
            flush=True,
        )
        ready_dir = export_root / "ready" / day
        staging_dir = export_root / "staging"
        ledger_path = ready_dir / "export-manifest.jsonl"

    pending = _list_all_pending(segment_root)
    print(f"pending_segments={len(pending)} ready_dir={ready_dir}", flush=True)
    if not pending:
        print("nothing to export", flush=True)
        return 0

    ok_n = 0
    fail_n = 0
    for seg_dir in pending:
        ok, msg = _export_one(
            seg_dir,
            ready_dir=ready_dir,
            staging_dir=staging_dir,
            ledger_path=ledger_path,
            dry_run=args.dry_run,
        )
        print(msg, flush=True)
        if ok:
            ok_n += 1
        else:
            fail_n += 1

    print(f"summary ok={ok_n} failed={fail_n} ready_dir={ready_dir}", flush=True)
    if ok_n:
        freed = max(0, bytes_before - _segment_bytes_before(segment_root))
        if freed:
            print(f"freed_bytes={freed}", flush=True)
    if fail_n:
        print(
            "FAILED segments remain uploaded=false in segments/; fix errors and re-run.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
