#!/usr/bin/env python3
"""QC gate: IMU alignment must be explicitly verified; never silent pass on host timestamp fallback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="IMU align verification gate")
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="emit status unverified without failing (candidate tier)",
    )
    args = parser.parse_args()
    report = json.loads(args.audit_report.read_text(encoding="utf-8"))
    status = (report.get("verification") or {}).get("status", "unverified")
    if status == "verified":
        print(json.dumps({"ok": True, "status": status}))
        return 0
    out = {"ok": False, "status": status, "reason": (report.get("verification") or {}).get("reason")}
    if args.allow_unverified:
        out["ok"] = True
        out["waived"] = True
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
