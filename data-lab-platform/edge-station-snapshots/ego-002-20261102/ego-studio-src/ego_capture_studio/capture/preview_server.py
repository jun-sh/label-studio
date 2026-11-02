"""Lightweight MJPEG HTTP server for 2×2 live preview (daemon threads)."""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    from ego_capture_studio.capture.preview_hub import (
        VALID_PREVIEW_CAMS,
        PreviewHub,
        resolve_preview_cam,
    )
except ImportError:
    from preview_hub import VALID_PREVIEW_CAMS, PreviewHub, resolve_preview_cam  # type: ignore[no-redef]

PREVIEW_FPS = float(os.environ.get("PREVIEW_FPS", "8"))
PREVIEW_HTTP_HOST = os.environ.get("PREVIEW_HTTP_HOST", "0.0.0.0")
PREVIEW_HTTP_PORT = int(os.environ.get("PREVIEW_HTTP_PORT", "8765"))
MJPEG_BOUNDARY = b"--datalabframe"


def _jpeg_relay_loop(hub: PreviewHub) -> None:
    """Encode latest RGB quad at PREVIEW_FPS (capture thread only offers RGB)."""
    from ego_capture_studio.capture.frame_jpeg_codec import encode_rgb_to_jpeg

    interval = 1.0 / max(PREVIEW_FPS, 0.5)
    while True:
        t0 = time.monotonic()
        pending = hub.take_pending()
        if pending:
            for short, rgb in pending.items():
                try:
                    hub.set_jpeg(short, encode_rgb_to_jpeg(rgb))
                except Exception:
                    pass
        else:
            pending_jpegs = hub.take_pending_jpegs()
            if pending_jpegs:
                for short, jpeg in pending_jpegs.items():
                    hub.set_jpeg(short, jpeg)
        elapsed = time.monotonic() - t0
        time.sleep(max(0.0, interval - elapsed))


def _make_handler(hub: PreviewHub):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _fmt, *_args) -> None:
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/preview/status":
                body = b'{"online":true,"fps":' + str(PREVIEW_FPS).encode() + b"}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            prefix = "/preview/"
            if not path.startswith(prefix):
                self.send_error(404)
                return
            rest = path[len(prefix) :]
            if rest.endswith("/mjpeg"):
                cam = rest[: -len("/mjpeg")]
                stream_mode = True
            elif rest.endswith("/jpg"):
                cam = rest[: -len("/jpg")]
                stream_mode = False
            else:
                self.send_error(404)
                return
            if cam not in VALID_PREVIEW_CAMS:
                self.send_error(404)
                return
            cam = resolve_preview_cam(cam)

            if not stream_mode:
                jpeg = hub.get_jpeg(cam)
                if not jpeg:
                    self.send_error(503, "no frame yet")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.send_header("Content-Length", str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)
                return

            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY.decode()}")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            frame_interval = 1.0 / max(PREVIEW_FPS, 0.5)
            while True:
                jpeg = hub.get_jpeg(cam)
                if jpeg:
                    header = (
                        MJPEG_BOUNDARY
                        + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n"
                    )
                    try:
                        self.wfile.write(header + jpeg + b"\r\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                time.sleep(frame_interval)

    return Handler


def _http_loop(hub: PreviewHub) -> None:
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            server = ThreadingHTTPServer(
                (PREVIEW_HTTP_HOST, PREVIEW_HTTP_PORT),
                _make_handler(hub),
            )
            server.daemon_threads = True
            server.serve_forever(poll_interval=0.5)
            return
        except OSError as exc:
            if getattr(exc, "errno", None) != 98:
                raise
            time.sleep(0.2)
    raise OSError(f"preview HTTP bind failed on {PREVIEW_HTTP_HOST}:{PREVIEW_HTTP_PORT}")


def start_preview_stack() -> PreviewHub:
    """Start relay + HTTP server; return hub for offer_jpegs()."""
    hub = PreviewHub()
    threading.Thread(target=_jpeg_relay_loop, args=(hub,), name="preview-jpeg-relay", daemon=True).start()
    threading.Thread(target=_http_loop, args=(hub,), name="preview-http", daemon=True).start()
    return hub
