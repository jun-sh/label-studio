"""Lightweight MJPEG HTTP server for 2×2 live preview (daemon threads)."""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

import cv2
import numpy as np

try:
    from ego_capture_studio.capture.preview_hub import PREVIEW_CAMERAS, VALID_PREVIEW_CAMS, PreviewHub
except ImportError:
    from preview_hub import PREVIEW_CAMERAS, VALID_PREVIEW_CAMS, PreviewHub

PREVIEW_FPS = float(os.environ.get("PREVIEW_FPS", "8"))
PREVIEW_MAX_EDGE = int(os.environ.get("PREVIEW_MAX_EDGE", "640"))
PREVIEW_JPEG_QUALITY = int(os.environ.get("PREVIEW_JPEG_QUALITY", "70"))
PREVIEW_HTTP_HOST = os.environ.get("PREVIEW_HTTP_HOST", "0.0.0.0")
PREVIEW_HTTP_PORT = int(os.environ.get("PREVIEW_HTTP_PORT", "8765"))
MJPEG_BOUNDARY = b"--datalabframe"


def _resize_to_max_edge(bgr: np.ndarray, max_edge: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    longest = max(h, w)
    if longest <= max_edge:
        return bgr
    scale = max_edge / float(longest)
    new_w = max(2, int(w * scale))
    new_h = max(2, int(h * scale))
    return cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _encoder_loop(hub: PreviewHub) -> None:
    interval = 1.0 / max(PREVIEW_FPS, 0.5)
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), PREVIEW_JPEG_QUALITY]
    while True:
        t0 = time.monotonic()
        pending = hub.take_pending()
        if pending:
            for short, _key in PREVIEW_CAMERAS:
                rgb = pending.get(short)
                if rgb is None:
                    continue
                bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                bgr = _resize_to_max_edge(bgr, PREVIEW_MAX_EDGE)
                ok, buf = cv2.imencode(".jpg", bgr, encode_params)
                if ok:
                    hub.set_jpeg(short, buf.tobytes())
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
            suffix = "/mjpeg"
            if not path.startswith(prefix) or not path.endswith(suffix):
                self.send_error(404)
                return
            cam = path[len(prefix) : -len(suffix)]
            if cam not in VALID_PREVIEW_CAMS:
                self.send_error(404)
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
    server = ThreadingHTTPServer((PREVIEW_HTTP_HOST, PREVIEW_HTTP_PORT), _make_handler(hub))
    server.daemon_threads = True
    server.serve_forever(poll_interval=0.5)


def start_preview_stack() -> PreviewHub:
    """Start encoder + HTTP server daemon threads; return hub for offer()."""
    hub = PreviewHub()
    threading.Thread(target=_encoder_loop, args=(hub,), name="preview-encoder", daemon=True).start()
    threading.Thread(target=_http_loop, args=(hub,), name="preview-http", daemon=True).start()
    return hub
