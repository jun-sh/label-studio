"""Derive-queue observability for 34 async path (symmetric to upload_status)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PHASE_LABELS_ZH: dict[str, str] = {
    "IDLE": "无派生任务",
    "UPLOADED": "已上传（处理中）",
    "DERIVING": "处理中",
    "READY": "已就绪（可回放）",
}

PHASE_LABELS_EN: dict[str, str] = {
    "IDLE": "No derive jobs",
    "UPLOADED": "Uploaded (derive pending)",
    "DERIVING": "Deriving on 34",
    "READY": "Ready (replayable)",
}


def default_station_id() -> str:
    return os.environ.get("EGO_STATION_ID", "ego-lan-214").strip() or "ego-lan-214"


def default_base_url() -> str:
    return (
        os.environ.get("EGO_DATALAB_BASE_URL", "").strip()
        or os.environ.get("DATA_LAB_PUBLIC_BASE_URL", "").strip()
        or "http://127.0.0.1:8080"
    ).rstrip("/")


def default_derive_status_url(*, station_id: str | None = None, session_id: str | None = None) -> str:
    explicit = os.environ.get("EGO_DERIVE_STATUS_URL", "").strip()
    if explicit:
        return explicit
    sid = station_id or default_station_id()
    base = default_base_url()
    url = f"{base}/lerobot/api/collection/stations/{sid}/derive-status"
    if session_id:
        url += f"?session={urllib.parse.quote(session_id)}"
    return url


def _progress_bar(completed: int, total: int, *, width: int = 22) -> str:
    if total <= 0:
        return f"[{'░' * width}] 0%"
    pct = min(100, round((completed / total) * 100))
    filled = int(width * pct / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct}%"


def _short_session_id(session_id: str) -> str:
    sid = (session_id or "—").strip()
    if len(sid) > 16:
        return f"{sid[:8]}…{sid[-8:]}"
    return sid


def _format_eta_zh(eta_seconds: Any) -> str:
    if not isinstance(eta_seconds, int) or eta_seconds <= 0:
        return ""
    if eta_seconds < 90:
        return f"剩余 ~{eta_seconds}s"
    mins = max(1, round(eta_seconds / 60))
    return f"剩余 ~{mins} 分钟"


def humanize_error(msg: str | None, *, lang: str = "zh") -> str:
    if not msg:
        return "未知错误" if lang.startswith("zh") else "unknown error"
    raw = str(msg)
    if lang.startswith("zh"):
        if "lock timeout" in raw.lower() or "jsonl.lock" in raw.lower():
            return "jsonl 锁超时（派生并发冲突，将自动重试）"
        if "derive validation failed" in raw.lower() or "frames" in raw.lower():
            return f"派生校验失败：{raw[:120]}"
        if "raw archive missing" in raw.lower() or "raw_not_found" in raw.lower():
            return "Raw 归档缺失"
        if "sha256" in raw.lower():
            return f"校验失败：{raw[:100]}"
        return raw[:160]
    return raw[:160]


def _two_phase_enabled() -> bool:
    v = os.environ.get("DERIVE_API_TWO_PHASE", "1").strip().lower()
    return v not in ("0", "false", "no")


def _external_phase(phase: str) -> str:
    if _two_phase_enabled() and phase == "DERIVING":
        return "UPLOADED"
    return phase


def datalab_root() -> Path | None:
    root = os.environ.get("EGO_DATALAB_ROOT", "").strip()
    return Path(root) if root else None


def _read_mux_failure(stream: Path) -> list[dict[str, Any]]:
    path = stream / "live" / "derive" / "mux_last_failure.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict) or not data.get("message"):
        return []
    return [
        {
            "segmentId": "session_mux",
            "errorMsg": str(data.get("message", ""))[:300],
            "stage": "mux",
        }
    ]


def _fetch_derive_status_api(
    *,
    url: str | None = None,
    station_id: str | None = None,
    session_id: str | None = None,
    timeout_s: float = 8.0,
) -> dict[str, Any] | None:
    target = url or default_derive_status_url(station_id=station_id, session_id=session_id)
    req = urllib.request.Request(target, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                data.setdefault("source", "api")
            return data
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError, OSError):
        return None


def load_derive_status_from_disk(station_id: str, root: Path) -> dict[str, Any] | None:
    """Disk-only fallback (UPLOADED | READY) — station-wide when multiple sessions exist."""
    stream = root / "data-storage" / "stream" / station_id
    raw_root = stream / "raw" / "segments"
    if not raw_root.is_dir():
        return None
    sessions = [p for p in raw_root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if not sessions:
        return None
    session_id = max(sessions, key=lambda p: sum(1 for _ in p.glob("*.tar.zst"))).name
    raw_total = sum(1 for p in sessions for _ in p.glob("*.tar.zst"))
    markers = 0
    for sess in sessions:
        markers_dir = stream / "live" / "derive" / "markers" / sess.name
        if markers_dir.is_dir():
            markers += sum(1 for _ in markers_dir.glob("*.ok.json"))
    activity_path = stream / "state" / "upload_activity.json"
    expected_total = raw_total
    if activity_path.is_file():
        try:
            activity = json.loads(activity_path.read_text(encoding="utf-8"))
            exp = int(activity.get("expectedSegmentTotal") or 0)
            if exp > 0:
                expected_total = max(exp, raw_total)
        except json.JSONDecodeError:
            pass
    info_path = stream / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
    parquet_rows = int(info.get("ingest_row_count") or info.get("total_frames") or 0)
    mux_path = stream / "live" / "derive" / "mux_validated.json"
    mux_val: dict[str, Any] = {}
    if mux_path.is_file():
        try:
            mux_val = json.loads(mux_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    mp4_ok = bool(mux_val.get("ok"))
    parquet_ready = raw_total > 0 and markers >= raw_total and parquet_rows > 0
    fully_ready = parquet_ready and mp4_ok
    if fully_ready:
        internal_phase = "READY"
    elif markers > 0 or (parquet_ready and not mp4_ok):
        internal_phase = "DERIVING"
    else:
        internal_phase = "UPLOADED"
    phase = _external_phase(internal_phase)
    sub_phase = None
    if not fully_ready and raw_total > 0:
        if markers < raw_total:
            sub_phase = "DERIVING_SEGMENTS"
        elif parquet_ready and not mp4_ok:
            sub_phase = "MUXING"
    percent = 100 if fully_ready else (min(99, round((markers / expected_total) * 100)) if expected_total else 0)
    return {
        "version": 3,
        "updatedAt": None,
        "stationId": station_id,
        "sessionId": session_id,
        "phase": phase,
        "asyncEnabled": True,
        "mode": "linear",
        "progress": {
            "total": expected_total,
            "uploaded": raw_total,
            "ready": raw_total if fully_ready else 0,
            "markers": markers,
            "parquetRows": parquet_rows,
            "parquetReady": parquet_ready,
            "mp4Ok": mp4_ok,
            "percent": percent,
            "remaining": 0 if fully_ready else max(0, raw_total - markers),
            "subPhase": sub_phase,
            "subPhaseLabelZh": (
                "视频合成中（MP4 多路编码）" if sub_phase == "MUXING" else
                "段级派生中" if sub_phase == "DERIVING_SEGMENTS" else None
            ),
            "subPhaseLabelEn": (
                "Muxing MP4 (multi-camera encode)" if sub_phase == "MUXING" else
                "Deriving segments" if sub_phase == "DERIVING_SEGMENTS" else None
            ),
        },
        "deriveQueue": {"stationId": station_id, "active": 0, "pending": 0, "mode": "linear"},
        "pipelineRunning": False,
        "failedSegments": _read_mux_failure(stream),
        "source": "disk",
        "logsHint": {
            "command": f"docker logs data-lab-stream-ingest-1 --since 2h 2>&1 | grep '{station_id}'",
        },
        "sla": {
            "UPLOADED": "Raw tar.zst verified on disk",
            "READY": "Parquet rows + MP4 frames validated on disk",
        },
    }


def fetch_derive_status(
    *,
    url: str | None = None,
    station_id: str | None = None,
    session_id: str | None = None,
    timeout_s: float = 8.0,
    retries: int = 3,
    retry_interval_s: float = 3.0,
) -> dict[str, Any] | None:
    sid = station_id or default_station_id()
    for attempt in range(max(1, retries)):
        data = _fetch_derive_status_api(
            url=url,
            station_id=sid,
            session_id=session_id,
            timeout_s=timeout_s,
        )
        if data:
            return data
        if attempt + 1 < retries:
            time.sleep(retry_interval_s)

    root = datalab_root()
    if root:
        disk = load_derive_status_from_disk(sid, root)
        if disk:
            print("[ego-derive] API 超时，已降级为磁盘直读状态", file=sys.stderr)
            return disk
    return None


def format_human_status(st: dict[str, Any] | None, *, lang: str = "zh") -> str:
    if not st:
        if lang.startswith("zh"):
            return (
                "无法获取派生状态（请确认 34 平台可访问，且 stream-ingest / Nginx 已启动）。\n"
                f"查询 URL：{default_derive_status_url()}"
            )
        return f"Cannot reach derive status at {default_derive_status_url()}"

    if lang.startswith("zh"):
        return _format_zh(st)
    return _format_en(st)


def _format_zh(st: dict[str, Any]) -> str:
    session = _short_session_id(str(st.get("sessionId") or "—"))
    phase = _external_phase(str(st.get("phase") or "IDLE"))
    prog = st.get("progress") or {}
    total = int(prog.get("total") or 0)
    markers = int(prog.get("markers") or 0)
    parquet_rows = int(prog.get("parquetRows") or 0)
    mp4_ok = bool(prog.get("mp4Ok"))
    sub_phase = prog.get("subPhase")

    lines: list[str] = [f"ego-derive · {session}"]

    if total <= 0:
        lines.append(f"状态：{PHASE_LABELS_ZH.get(phase, phase)}")
        return "\n".join(lines)

    done = total if phase == "READY" else markers
    bar = _progress_bar(done, total)

    if phase == "READY":
        lines.append(f"{bar} · 已就绪 {total}/{total} 段 · 可回放")
    elif sub_phase == "MUXING" or (markers >= total and parquet_rows > 0 and not mp4_ok):
        pct = int(prog.get("percent") or 99)
        bar = _progress_bar(min(total, max(0, round(total * pct / 100))), total)
        if prog.get("pipelineRunning"):
            lines.append(f"{bar} · MP4 合成中 · parquet {parquet_rows} 行")
        else:
            lines.append(f"{bar} · MP4 帧数校验未过（需在服务器排查）")
    else:
        lines.append(f"{bar} · 派生 {markers}/{total} 段")

    failed = st.get("failedSegments") or []
    for item in failed[:1]:
        if isinstance(item, dict) and item.get("errorMsg"):
            lines.append(f"✗ {humanize_error(str(item.get('errorMsg')))}")

    if st.get("source") == "disk":
        lines.append("ℹ 磁盘直读（API 繁忙）")

    return "\n".join(lines)


def _format_en(st: dict[str, Any]) -> str:
    lines: list[str] = []
    station = st.get("stationId") or default_station_id()
    session = st.get("sessionId") or "—"
    phase = str(st.get("phase") or "IDLE")
    prog = st.get("progress") or {}
    ready = int(prog.get("ready") or 0)
    total = int(prog.get("total") or 0)
    lines.append(f"ego-derive · {station} · {session}")
    lines.append(f"Phase: {PHASE_LABELS_EN.get(phase, phase)}")
    lines.append(f"Progress: {ready}/{total} segments ready ({prog.get('percent', 0)}%)")
    if st.get("etaSeconds"):
        lines.append(f"ETA: ~{st['etaSeconds']}s")
    for item in (st.get("failedSegments") or [])[:5]:
        if isinstance(item, dict):
            lines.append(f"FAIL {item.get('segmentId')}: {item.get('errorMsg')}")
    return "\n".join(lines)


def paint_live_status(
    *,
    url: str | None = None,
    station_id: str | None = None,
    session_id: str | None = None,
    lang: str = "zh",
    prev_lines: int = 0,
    finalize: bool = False,
    fast_fetch: bool = False,
) -> int:
    if fast_fetch:
        st = fetch_derive_status(
            url=url,
            station_id=station_id,
            session_id=session_id,
            timeout_s=4.0,
            retries=1,
            retry_interval_s=0,
        )
    else:
        st = fetch_derive_status(url=url, station_id=station_id, session_id=session_id)
    text = format_human_status(st, lang=lang)
    lines = text.split("\n")
    if prev_lines > 0:
        sys.stdout.write(f"\033[{prev_lines}A")
    for line in lines:
        sys.stdout.write("\033[K" + line + "\n")
    extra = max(0, prev_lines - len(lines))
    if finalize or fast_fetch:
        sys.stdout.write("\033[J")
    elif extra > 0:
        for _ in range(extra):
            sys.stdout.write("\033[K\n")
    sys.stdout.flush()
    return len(lines)


class LiveDeriveStatusPrinter:
    """Poll derive-status API and repaint terminal panel (like ego-upload live UI)."""

    def __init__(
        self,
        *,
        interval_s: float = 2.0,
        url: str | None = None,
        station_id: str | None = None,
        session_id: str | None = None,
        lang: str = "zh",
    ) -> None:
        self.interval_s = max(1.0, interval_s)
        self.url = url
        self.station_id = station_id
        self.session_id = session_id
        self.lang = lang
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._prev_lines = 0

    def __enter__(self) -> LiveDeriveStatusPrinter:
        if not sys.stdout.isatty():
            return self
        self._thread = threading.Thread(target=self._loop, name="ego-derive-live-ui", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_s + 1.0)
        if sys.stdout.isatty():
            paint_live_status(
                url=self.url,
                station_id=self.station_id,
                session_id=self.session_id,
                lang=self.lang,
                prev_lines=self._prev_lines,
                finalize=True,
            )
            self._prev_lines = 0

    def _loop(self) -> None:
        while not self._stop.is_set():
            if sys.stdout.isatty():
                self._prev_lines = paint_live_status(
                    url=self.url,
                    station_id=self.station_id,
                    session_id=self.session_id,
                    lang=self.lang,
                    prev_lines=self._prev_lines,
                    fast_fetch=True,
                )
            if self._stop.wait(self.interval_s):
                break


def upload_complete_derive_hint(*, lang: str = "zh") -> str:
    if lang.startswith("zh"):
        return "→ 数据已送达服务器，后台派生中请耐心等待。"
    return "Data delivered; derive running on server — please wait."
