"""CLI: human-readable derive status from 34 derive-status API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    from ego_capture_studio.capture.derive_status import (
        LiveDeriveStatusPrinter,
        default_derive_status_url,
        default_station_id,
        fetch_derive_status,
        format_human_status,
    )
except ImportError:
    from derive_status import (  # type: ignore[no-redef]
        LiveDeriveStatusPrinter,
        default_derive_status_url,
        default_station_id,
        fetch_derive_status,
        format_human_status,
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description="Show EGO segment derive progress on Data Lab (34 async path).",
    )
    p.add_argument("--station", default="", help="Station id (default: EGO_STATION_ID)")
    p.add_argument("--session", default="", help="Filter by session id")
    p.add_argument("--url", default="", help="Override derive-status URL")
    p.add_argument("--json", action="store_true", help="Print raw JSON")
    p.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="Live terminal panel (poll every 2s until Ctrl+C)",
    )
    p.add_argument(
        "--lang",
        choices=("zh", "en"),
        default=os.environ.get("EGO_DERIVE_STATUS_LANG", "zh"),
    )
    args = p.parse_args()

    station_id = args.station.strip() or default_station_id()
    session_id = args.session.strip() or None
    url = args.url.strip() or None

    if args.watch:
        if not sys.stdout.isatty():
            print("--watch requires a TTY", file=sys.stderr)
            raise SystemExit(2)
        try:
            with LiveDeriveStatusPrinter(
                url=url,
                station_id=station_id,
                session_id=session_id,
                lang=args.lang,
            ):
                while True:
                    time.sleep(3600)
        except KeyboardInterrupt:
            print(file=sys.stderr)
        return

    st = fetch_derive_status(url=url, station_id=station_id, session_id=session_id)
    if args.json:
        print(json.dumps(st or {}, ensure_ascii=False, indent=2))
        raise SystemExit(0 if st else 1)

    print(format_human_status(st, lang=args.lang))
    if st is None:
        print(f"\nURL: {url or default_derive_status_url(station_id=station_id, session_id=session_id)}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
