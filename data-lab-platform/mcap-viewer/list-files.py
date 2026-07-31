#!/usr/bin/env python3
"""List .mcap files under /data for the MCAP viewer file browser."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA_ROOT = Path(os.environ.get("MCAP_DATA_ROOT", "/data"))


def iter_mcap_files() -> list[dict[str, object]]:
    if not DATA_ROOT.is_dir():
        return []
    out: list[dict[str, object]] = []
    for path in sorted(DATA_ROOT.rglob("*.mcap")):
        rel = path.relative_to(DATA_ROOT).as_posix()
        stat = path.stat()
        out.append({"path": rel, "size": stat.st_size, "mtime": stat.st_mtime})
    return out


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/api/files":
            self.send_error(404)
            return
        body = json.dumps(iter_mcap_files()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    port = int(os.environ.get("MCAP_API_PORT", "8878"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"mcap file api on :{port} root={DATA_ROOT}", flush=True)
    httpd.serve_forever()
