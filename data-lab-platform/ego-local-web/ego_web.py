#!/usr/bin/env python3
"""EGO edge local capture control — stdlib-only HTTP server (mobile web UI)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Config (override via environment)
# ---------------------------------------------------------------------------
HOST = os.environ.get("EGO_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("EGO_WEB_PORT", "8080"))
CAPTURE_TARGET = os.environ.get("EGO_CAPTURE_TARGET", "ecs-oak-capture-stack.target")
CAPTURE_RECORD_UNIT = os.environ.get("EGO_CAPTURE_RECORD_UNIT", "ecs-record-oak-stream.service")
SEGMENT_ACTIVE_ROOT = Path(
    os.environ.get("EGO_SEGMENT_ACTIVE_ROOT", "/dev/shm/ego-capture-active"),
)
SEGMENT_ROOT = Path(
    os.environ.get("EGO_SEGMENT_ROOT", "/home/server/cache/ego-lan-214/segments"),
)
PREVIEW_URL = os.environ.get(
    "EGO_PREVIEW_URL",
    "http://127.0.0.1:8765/preview/front_left/jpg",
)
STORAGE_WARN_GB = float(os.environ.get("EGO_STORAGE_WARN_GB", "2"))
START_TIMEOUT_S = float(os.environ.get("EGO_CAPTURE_START_TIMEOUT_S", "45"))
STOP_TIMEOUT_S = float(os.environ.get("EGO_CAPTURE_STOP_TIMEOUT_S", "120"))
MIN_ACTION_INTERVAL_S = float(os.environ.get("EGO_MIN_ACTION_INTERVAL_S", "3"))

_lock = threading.Lock()
_busy = False
_busy_action: str | None = None
_last_action_mono = 0.0
_last_error = ""
_capture_writing_since: float | None = None


def _unit_active_since_epoch(unit: str) -> float | None:
    proc = _systemctl(
        "show",
        unit,
        "--property=ActiveEnterTimestamp",
        "--value",
        timeout=5,
    )
    raw = proc.stdout.strip()
    if not raw or raw == "n/a":
        return None
    try:
        epoch = subprocess.run(
            ["date", "-d", raw, "+%s"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if epoch.returncode == 0 and epoch.stdout.strip().isdigit():
            return float(epoch.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        dt = datetime.strptime(raw.rsplit(" ", 1)[0], "%a %Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _manifest_created_epoch(data: dict[str, Any]) -> float | None:
    raw = data.get("created_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        if raw.endswith("Z"):
            dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _capture_run_since_epoch() -> float | None:
    since = _unit_active_since_epoch(CAPTURE_RECORD_UNIT)
    if since is not None:
        return since
    return _unit_active_since_epoch(CAPTURE_TARGET)


def _shm_open_segment_bin_count(since_epoch: float | None) -> int:
    """Count frame bins only in open segments started during the current capture run."""
    sessions = SEGMENT_ACTIVE_ROOT / "sessions"
    if not sessions.is_dir():
        return 0
    best = 0
    for manifest_path in sessions.glob("*/segments/seg_*/manifest.json"):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("closed"):
            continue
        if since_epoch is not None:
            created = _manifest_created_epoch(data)
            # Ignore stale shm dirs left from prior failed stops.
            if created is None or created < since_epoch - 2.0:
                continue
        frames_dir = manifest_path.parent / "frames"
        if frames_dir.is_dir():
            best = max(best, sum(1 for _ in frames_dir.glob("*.bin")))
    return best


def _journal_has_capture_only_since(since_epoch: float | None) -> bool:
    if since_epoch is None:
        return False
    since_local = datetime.fromtimestamp(since_epoch).strftime("%Y-%m-%d %H:%M:%S")
    proc = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            CAPTURE_RECORD_UNIT,
            f"--since={since_local}",
            "-n",
            "80",
            "--no-pager",
            "-o",
            "cat",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return proc.returncode == 0 and "capture-only session=" in proc.stdout


def _capture_frames_writing() -> bool:
    since = _capture_run_since_epoch()
    if _journal_has_capture_only_since(since):
        return True
    return _shm_open_segment_bin_count(since) > 0


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _systemctl(*args: str, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _capture_active() -> bool:
    proc = _systemctl("is-active", CAPTURE_TARGET, timeout=5)
    return proc.stdout.strip() == "active"


def _format_free_gb(path: Path) -> str:
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        gb = usage.free / (1024**3)
        return f"{gb:.1f} GB"
    except OSError:
        return "—"


def _storage_free_bytes(path: Path) -> int | None:
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        return int(usage.free)
    except OSError:
        return None


def _count_segments(root: Path) -> int:
    sessions = root / "sessions"
    if not sessions.is_dir():
        return 0
    count = 0
    for sess in sessions.iterdir():
        if not sess.is_dir():
            continue
        seg_root = sess / "segments"
        if not seg_root.is_dir():
            continue
        for seg in seg_root.iterdir():
            if seg.is_dir() and seg.name.startswith("seg_"):
                count += 1
    return count


def _build_status() -> dict[str, Any]:
    global _busy, _busy_action, _last_error, _capture_writing_since

    with _lock:
        busy = _busy
        busy_action = _busy_action
        err = _last_error

    active = _capture_active()
    frames_writing = active and _capture_frames_writing()

    if not active:
        _capture_writing_since = None

    duration = 0
    if active and frames_writing:
        if _capture_writing_since is None:
            _capture_writing_since = time.time()
        duration = max(0, int(time.time() - _capture_writing_since))

    free_bytes = _storage_free_bytes(SEGMENT_ROOT)
    storage_warn = (
        free_bytes is not None and free_bytes < int(STORAGE_WARN_GB * (1024**3))
    )

    if busy and busy_action == "start":
        state = "starting"
        msg = "正在启动采集服务，请稍候…"
    elif busy and busy_action == "stop":
        state = "stopping"
        msg = "正在保存数据，请勿断电…"
    elif err:
        state = "error"
        msg = err
    elif active and frames_writing:
        state = "recording"
        msg = ""
    elif active:
        state = "warming"
        msg = "相机初始化中，开始写入数据后计时"
    else:
        state = "idle"
        msg = ""

    return {
        "state": state,
        "duration": duration,
        "storage_free": _format_free_gb(SEGMENT_ROOT),
        "storage_warn": storage_warn,
        "segment_count": _count_segments(SEGMENT_ROOT),
        "msg": msg,
        "capture_active": active,
        "frames_writing": frames_writing,
    }


def _run_capture_action(action: str) -> tuple[bool, str]:
    global _busy, _busy_action, _last_error, _last_action_mono

    now = time.monotonic()
    with _lock:
        if _busy:
            return False, "操作进行中，请稍候"
        if now - _last_action_mono < MIN_ACTION_INTERVAL_S:
            return False, "操作过快，请稍后再试"
        _busy = True
        _busy_action = action
        _last_error = ""

    try:
        if action == "start":
            if _capture_active():
                return True, "采集已在运行"
            proc = _systemctl("start", CAPTURE_TARGET, timeout=START_TIMEOUT_S)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "启动失败").strip()
                _last_error = f"无法启动采集：{detail}"
                return False, _last_error
            deadline = time.monotonic() + START_TIMEOUT_S
            while time.monotonic() < deadline:
                if _capture_active():
                    return True, ""
                time.sleep(0.5)
            _last_error = "相机启动超时，请检查 OAK 设备是否连接"
            return False, _last_error

        if action == "stop":
            if not _capture_active():
                ok = True
            else:
                proc = _systemctl("stop", CAPTURE_TARGET, timeout=STOP_TIMEOUT_S)
                if proc.returncode != 0:
                    detail = (proc.stderr or proc.stdout or "停止失败").strip()
                    _last_error = f"无法停止采集：{detail}"
                    return False, _last_error
                deadline = time.monotonic() + STOP_TIMEOUT_S
                ok = False
                while time.monotonic() < deadline:
                    if not _capture_active():
                        ok = True
                        break
                    time.sleep(0.5)
                if not ok:
                    _last_error = "停止超时，请稍后刷新页面查看状态"
                    return False, _last_error
            return True, ""

        return False, "未知操作"
    finally:
        with _lock:
            _busy = False
            _busy_action = None
            _last_action_mono = time.monotonic()


def _fetch_preview() -> tuple[bytes | None, str]:
    req = urllib.request.Request(PREVIEW_URL, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.read(), resp.headers.get("Content-Type", "image/jpeg")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, "image/jpeg"


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="theme-color" content="#EEF4FF" />
  <title>EGO 采集</title>
  <style>
    :root {
      --bg: #f4f3ef;
      --text: #1a1a18;
      --muted: #6b6b66;
      --idle-bg: #EEF4FF;
      --idle-text: #1E3A8A;
      --idle-border: #BFDBFE;
      --rec: #1f8a4c;
      --err: #c0392b;
      --warn: #b45309;
      --btn-idle: #2563eb;
      --btn-stop: #dc2626;
      --radius: 16px;
      --shadow: 0 8px 28px rgba(0,0,0,.12);
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0;
      font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg); color: var(--text);
      min-height: 100dvh;
    }
    .app {
      max-width: 480px; margin: 0 auto;
      padding: 12px 16px calc(20px + env(safe-area-inset-bottom));
      display: flex; flex-direction: column; gap: 16px;
    }
    .status {
      border-radius: var(--radius);
      padding: 0 16px;
      min-height: 96px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      box-shadow: var(--shadow);
      transition: background .25s, color .25s;
    }
    .status[data-state="idle"] {
      background: var(--idle-bg);
      color: var(--idle-text);
      border: 1px solid var(--idle-border);
    }
    .status[data-state="recording"] { background: var(--rec); color: #fff; padding: 18px 16px; }
    .status[data-state="starting"],
    .status[data-state="warming"],
    .status[data-state="stopping"] {
      background: #b8860b;
      color: #fff;
      padding: 18px 16px;
    }
    .status[data-state="error"] { background: var(--err); color: #fff; padding: 18px 16px; }
    .status-title {
      font-size: 1.5rem;
      font-weight: 700;
      letter-spacing: .04em;
      line-height: 1.3;
      width: 100%;
      margin: 0;
    }
    .status[data-state="idle"] .status-title {
      font-size: 1.6rem;
      line-height: 1.25;
      padding: 0;
    }
  /* 待机无副文案：主标题在条内垂直正中 */
    .status[data-state="idle"].status--solo .status-title {
      min-height: 96px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .status-sub {
      margin: 0;
      padding: 0 0 14px;
      font-size: .95rem;
      width: 100%;
    }
    .status-sub:empty {
      display: none;
      min-height: 0;
      padding: 0;
    }
    .status[data-state="idle"] .status-sub:not(:empty) {
      color: #3B5B9A;
      opacity: .9;
      padding-bottom: 16px;
    }
    .status:not([data-state="idle"]) .status-sub {
      margin-top: 6px;
      padding-bottom: 14px;
      opacity: .95;
    }
    .spinner {
      display: inline-block; width: 18px; height: 18px;
      border: 2px solid rgba(255,255,255,.35);
      border-top-color: #fff; border-radius: 50%;
      animation: spin .8s linear infinite;
      vertical-align: -3px; margin-right: 6px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .preview-wrap {
      background: #111; border-radius: var(--radius);
      overflow: hidden; aspect-ratio: 4/3;
      position: relative; box-shadow: var(--shadow);
    }
    .preview-wrap img {
      width: 100%; height: 100%; object-fit: cover; display: block;
      background: #222;
    }
    .preview-placeholder {
      position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center;
      color: #aaa; font-size: .95rem; padding: 16px; text-align: center;
    }

    .btn-main {
      width: 100%; min-height: 64px;
      border: none; border-radius: 999px;
      font-size: 1.35rem; font-weight: 700;
      color: #fff; cursor: pointer;
      box-shadow: var(--shadow);
      transition: transform .1s, opacity .2s, background .2s;
      -webkit-tap-highlight-color: transparent;
      touch-action: manipulation;
    }
    .btn-main:active:not(:disabled) { transform: scale(.98); }
    .btn-main:disabled { opacity: .55; cursor: not-allowed; }
    .btn-main[data-mode="start"] { background: var(--btn-idle); }
    .btn-main[data-mode="stop"] { background: var(--btn-stop); }

    .info {
      display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
    }
    .info-card {
      background: #fff; border-radius: 12px; padding: 12px 14px;
      box-shadow: 0 2px 10px rgba(0,0,0,.06);
    }
    .info-label { font-size: .78rem; color: var(--muted); }
    .info-value { font-size: 1.1rem; font-weight: 600; margin-top: 4px; }
    .info-value.warn { color: var(--warn); }

    .hint {
      font-size: .8rem; color: var(--muted); text-align: center; line-height: 1.5;
      padding: 0 8px;
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="status" id="statusBar" data-state="idle">
      <div class="status-title" id="statusTitle">设备待机中</div>
      <div class="status-sub" id="statusSub"></div>
    </header>

    <section class="preview-wrap" aria-label="实时预览">
      <img id="previewImg" alt="" hidden />
      <div class="preview-placeholder" id="previewPh">开始录制后显示实时画面</div>
    </section>

    <button type="button" class="btn-main" id="mainBtn" data-mode="start">开始录制</button>

    <section class="info">
      <div class="info-card">
        <div class="info-label">磁盘剩余</div>
        <div class="info-value" id="storageFree">—</div>
      </div>
      <div class="info-card">
        <div class="info-label">已生成段数</div>
        <div class="info-value" id="segmentCount">0</div>
      </div>
    </section>

    <p class="hint">结束录制前请保持与本设备的 WiFi 连接。<br />录制过程中手机断开不影响数据写入。</p>
  </div>
  <script>
(function () {
  var statusBar = document.getElementById("statusBar");
  var statusTitle = document.getElementById("statusTitle");
  var statusSub = document.getElementById("statusSub");
  var mainBtn = document.getElementById("mainBtn");
  var previewImg = document.getElementById("previewImg");
  var previewPh = document.getElementById("previewPh");
  var storageFree = document.getElementById("storageFree");
  var segmentCount = document.getElementById("segmentCount");

  var pollTimer = null;
  var previewTimer = null;
  var actionInFlight = false;

  function formatDuration(sec) {
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  function applyStatus(data) {
    var st = data.state || "idle";
    statusBar.setAttribute("data-state", st);

    if (st === "idle") {
      statusTitle.textContent = "设备待机中";
      statusSub.textContent = data.msg || "";
      mainBtn.textContent = "开始录制";
      mainBtn.setAttribute("data-mode", "start");
      if (data.msg) {
        statusBar.classList.remove("status--solo");
      } else {
        statusBar.classList.add("status--solo");
      }
    } else if (st === "warming") {
      statusBar.classList.remove("status--solo");
      statusTitle.innerHTML = '<span class="spinner"></span>正在准备录制';
      statusSub.textContent = data.msg || "相机初始化中，开始写入后计时";
      mainBtn.textContent = "结束录制";
      mainBtn.setAttribute("data-mode", "stop");
    } else if (st === "recording") {
      statusBar.classList.remove("status--solo");
      statusTitle.textContent = "正在录制中";
      statusSub.textContent = formatDuration(data.duration || 0);
      mainBtn.textContent = "结束录制";
      mainBtn.setAttribute("data-mode", "stop");
    } else if (st === "starting") {
      statusBar.classList.remove("status--solo");
      statusTitle.innerHTML = '<span class="spinner"></span>正在启动相机';
      statusSub.textContent = data.msg || "请稍候，约需数秒";
      mainBtn.disabled = true;
    } else if (st === "stopping") {
      statusBar.classList.remove("status--solo");
      statusTitle.innerHTML = '<span class="spinner"></span>正在保存数据';
      statusSub.textContent = data.msg || "请勿断电";
      mainBtn.disabled = true;
    } else if (st === "error") {
      statusBar.classList.remove("status--solo");
      statusTitle.textContent = "出现问题";
      statusSub.textContent = data.msg || "请稍后重试";
      mainBtn.textContent = data.capture_active ? "结束录制" : "开始录制";
      mainBtn.setAttribute("data-mode", data.capture_active ? "stop" : "start");
    }

    if (st !== "starting" && st !== "stopping" && !actionInFlight) {
      mainBtn.disabled = false;
    }

    storageFree.textContent = data.storage_free || "—";
    storageFree.className = "info-value" + (data.storage_warn ? " warn" : "");
    segmentCount.textContent = String(data.segment_count != null ? data.segment_count : 0);

    if (st === "recording" || st === "warming") {
      startPreview();
    } else if (st !== "starting") {
      stopPreview();
    }
  }

  function fetchStatus() {
    return fetch("/api/status", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(applyStatus)
      .catch(function () {
        statusBar.setAttribute("data-state", "error");
        statusTitle.textContent = "无法连接设备";
        statusSub.textContent = "请确认已连接 EGO WiFi";
      });
  }

  function tickPreview() {
    if (previewImg.hidden) {
      previewImg.hidden = false;
      previewPh.hidden = true;
    }
    previewImg.src = "/api/preview/main.jpg?t=" + Date.now();
  }

  function startPreview() {
    if (previewTimer) return;
    tickPreview();
    previewTimer = setInterval(tickPreview, 400);
  }

  function stopPreview() {
    if (previewTimer) {
      clearInterval(previewTimer);
      previewTimer = null;
    }
    previewImg.hidden = true;
    previewPh.hidden = false;
    previewImg.removeAttribute("src");
  }

  function doAction(path) {
    if (actionInFlight) return;
    actionInFlight = true;
    mainBtn.disabled = true;
    fetch(path, { method: "POST", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success && res.msg) {
          statusBar.setAttribute("data-state", "error");
          statusTitle.textContent = "操作失败";
          statusSub.textContent = res.msg;
        }
        return fetchStatus();
      })
      .finally(function () {
        actionInFlight = false;
      });
  }

  mainBtn.addEventListener("click", function () {
    var mode = mainBtn.getAttribute("data-mode");
    if (mode === "stop") {
      doAction("/api/capture/stop");
    } else {
      doAction("/api/capture/start");
    }
  });

  fetchStatus();
  pollTimer = setInterval(fetchStatus, 800);
})();
  </script>
</body>
</html>
"""


class EgoWebHandler(BaseHTTPRequestHandler):
    server_version = "ego-web/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/status":
            _json_response(self, HTTPStatus.OK, _build_status())
            return

        if path == "/api/preview/main.jpg":
            data, ctype = _fetch_preview()
            if not data:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "preview unavailable")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        if length:
            _ = self.rfile.read(length)

        if path == "/api/capture/start":
            ok, msg = _run_capture_action("start")
            _json_response(
                self,
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
                {"success": ok, "msg": msg},
            )
            return

        if path == "/api/capture/stop":
            ok, msg = _run_capture_action("stop")
            _json_response(
                self,
                HTTPStatus.OK if ok else HTTPStatus.CONFLICT,
                {"success": ok, "msg": msg},
            )
            return

        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), EgoWebHandler)
    print(f"ego-web listening on http://{HOST}:{PORT}", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
