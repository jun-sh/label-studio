"""CLI: human-readable upload status from upload-status.json (P0 commercial delivery)."""

from __future__ import annotations

import argparse
import json
import os
import sys

from ego_capture_studio.capture.upload_status import (
    default_status_path,
    format_human_status,
    read_status,
)


def main() -> None:
    p = argparse.ArgumentParser(description="Show EGO segment upload status (ecs-upload-loop).")
    p.add_argument(
        "--path",
        type=str,
        default="",
        help="Override status file path (default: EGO_UPLOAD_STATUS_PATH or XDG_RUNTIME_DIR)",
    )
    p.add_argument("--json", action="store_true", help="Print raw JSON")
    p.add_argument("--lang", choices=("zh", "en"), default=os.environ.get("EGO_UPLOAD_STATUS_LANG", "zh"))
    args = p.parse_args()

    path = default_status_path() if not args.path else __import__("pathlib").Path(args.path)
    st = read_status(path)
    if args.json:
        if st is None:
            print("{}")
            return
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return

    print(format_human_status(st, lang=args.lang))
    if st is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
