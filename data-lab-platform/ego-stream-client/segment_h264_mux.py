"""Mux in-memory H.264 NAL chunks into per-camera MP4 at segment close (single IO burst)."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

SEGMENT_H264_FPS = int(os.environ.get("OAK_DEVICE_FPS", "30"))
SEGMENT_H264_MIN_MP4 = max(1, int(os.environ.get("SEGMENT_H264_MIN_MP4", "4")))
SEGMENT_H264_STRICT = os.environ.get("SEGMENT_H264_STRICT", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
SEGMENT_H264_MUX_MODE = os.environ.get(
    "SEGMENT_H264_MUX_MODE",
    "copy" if SEGMENT_H264_STRICT else "copy",
).strip().lower()
SEGMENT_H264_REENCODE_PRESET = os.environ.get("SEGMENT_H264_REENCODE_PRESET", "veryfast").strip()
SEGMENT_H264_PARITY_TOLERANCE = max(0, int(os.environ.get("SEGMENT_H264_PARITY_TOLERANCE", "0")))


def _segment_h264_enabled() -> bool:
    return os.environ.get("SEGMENT_H264", "0").strip().lower() in ("1", "true", "yes")


def _ffmpeg_input_flags() -> list[str]:
    raw = os.environ.get("SEGMENT_H264_FFMPEG_INPUT_FLAGS", "").strip()
    if raw:
        return shlex.split(raw)
    return ["-fflags", "+genpts", "-avoid_negative_ts", "make_zero"]


def resolve_ffmpeg() -> str:
    """Return ffmpeg binary path (130 capture host must have ffmpeg in PATH)."""
    explicit = os.environ.get("FFMPEG", "").strip()
    if explicit:
        return explicit
    found = shutil.which("ffmpeg")
    if not found:
        raise FileNotFoundError(
            "ffmpeg not found on capture host; install ffmpeg or set FFMPEG=/path/to/ffmpeg"
        )
    return found


def preflight_segment_h264_capture() -> str:
    """Startup check when SEGMENT_H264=1 (requires ffmpeg on capture host)."""
    if not _segment_h264_enabled():
        return ""
    path = resolve_ffmpeg()
    print(
        f"segment_h264_preflight ok ffmpeg={path} mux_mode={SEGMENT_H264_MUX_MODE} "
        f"strict={SEGMENT_H264_STRICT}",
        flush=True,
    )
    return path


def _probe_mp4_frame_count(mp4_path: Path) -> int:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_packets",
            "-show_entries",
            "stream=nb_read_packets,nb_read_frames,nb_frames",
            "-of",
            "json",
            str(mp4_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {mp4_path}: {proc.stderr[-300:]}")
    stream = json.loads(proc.stdout or "{}").get("streams", [{}])[0]
    for key in ("nb_read_packets", "nb_read_frames", "nb_frames"):
        val = stream.get(key)
        if val is not None and str(val).isdigit():
            n = int(val)
            if n > 0:
                return n
    return 0


def _uniform_chunk_count(streams: dict[str, list[bytes]]) -> int:
    counts = {key: len(chunks) for key, chunks in streams.items() if chunks}
    if not counts:
        return 0
    vals = list(counts.values())
    if len(set(vals)) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        raise RuntimeError(f"h264_chunk_count_mismatch {detail}")
    return vals[0]


def verify_segment_stream_mp4s(
    segment_dir: Path,
    *,
    min_mp4: int | None = None,
) -> list[Path]:
    """Raise if segment H264 outputs are missing or undersized."""
    if not _segment_h264_enabled():
        return []
    streams_dir = Path(segment_dir) / "streams"
    mp4s = sorted(streams_dir.glob("*.mp4")) if streams_dir.is_dir() else []
    need = SEGMENT_H264_MIN_MP4 if min_mp4 is None else max(1, int(min_mp4))
    if len(mp4s) < need:
        raise RuntimeError(
            f"segment_stream_mp4_insufficient segment={Path(segment_dir).name} "
            f"got={len(mp4s)} need>={need}"
        )
    for mp4_path in mp4s:
        if mp4_path.stat().st_size < 1024:
            raise RuntimeError(f"segment_stream_mp4_empty path={mp4_path}")
    return mp4s


def _trim_mp4_to_frame_count(mp4_path: Path, frame_count: int) -> None:
    ffmpeg = resolve_ffmpeg()
    tmp = mp4_path.with_suffix(f".trim.{os.getpid()}.mp4")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(mp4_path),
        "-frames:v",
        str(frame_count),
        "-c:v",
        "libx264",
        "-preset",
        SEGMENT_H264_REENCODE_PRESET,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    subprocess.run(cmd, check=True, timeout=300)
    tmp.replace(mp4_path)


def enforce_segment_frame_parity(
    segment_dir: Path,
    row_count: int,
    *,
    mp4_paths: list[Path] | None = None,
) -> int:
    """Trim rows + per-camera MP4 to a common frame budget (min across cameras)."""
    paths = mp4_paths or verify_segment_stream_mp4s(segment_dir)
    counts = {p.name: _probe_mp4_frame_count(p) for p in paths}
    vals = [int(row_count), *[int(v) for v in counts.values() if v > 0]]
    target = min(vals)
    if target <= 0:
        raise RuntimeError(f"segment_frame_parity_invalid target={target} counts={counts}")

    rows_path = Path(segment_dir) / "rows.jsonl"
    lines = [ln for ln in rows_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) > target:
        lines = lines[:target]
        rows_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    elif len(lines) < target:
        target = len(lines)

    for mp4_path in paths:
        n = int(counts.get(mp4_path.name) or 0)
        if n > target:
            _trim_mp4_to_frame_count(mp4_path, target)
        elif n < target - SEGMENT_H264_PARITY_TOLERANCE:
            raise RuntimeError(
                f"segment_mp4_short_read path={mp4_path.name} got={n} need={target}"
            )

    final = {p.name: _probe_mp4_frame_count(p) for p in paths}
    spread = max(final.values()) - min(final.values())
    if spread > SEGMENT_H264_PARITY_TOLERANCE:
        raise RuntimeError(
            f"segment_mp4_camera_spread segment={Path(segment_dir).name} "
            f"counts={final} spread={spread} tolerance={SEGMENT_H264_PARITY_TOLERANCE}"
        )
    if min(final.values()) != target:
        raise RuntimeError(
            f"segment_frame_parity_align_failed target={target} final={final}"
        )
    return target


def verify_segment_frame_parity(
    segment_dir: Path,
    expected_rows: int,
    *,
    mp4_paths: list[Path] | None = None,
) -> dict[str, int]:
    """Fail when rows.jsonl count and decodable MP4 packets diverge beyond tolerance."""
    if expected_rows <= 0:
        raise RuntimeError(f"segment_frame_parity_invalid expected_rows={expected_rows}")
    paths = mp4_paths or verify_segment_stream_mp4s(segment_dir)
    counts = {p.name: _probe_mp4_frame_count(p) for p in paths}
    if not counts:
        raise RuntimeError(f"segment_frame_parity_no_mp4 segment={segment_dir.name}")
    vals = list(counts.values())
    spread = max(vals) - min(vals)
    if spread > SEGMENT_H264_PARITY_TOLERANCE:
        raise RuntimeError(
            f"segment_mp4_camera_spread segment={segment_dir.name} "
            f"counts={counts} spread={spread} tolerance={SEGMENT_H264_PARITY_TOLERANCE}"
        )
    video_frames = min(vals)
    delta = abs(int(expected_rows) - int(video_frames))
    if delta > SEGMENT_H264_PARITY_TOLERANCE:
        raise RuntimeError(
            f"segment_frame_parity_mismatch segment={segment_dir.name} "
            f"rows={expected_rows} video={video_frames} delta={delta} "
            f"tolerance={SEGMENT_H264_PARITY_TOLERANCE}"
        )
    return counts


def _mux_one_camera(
    ffmpeg: str,
    h264_path: Path,
    mp4_path: Path,
    *,
    rate: int,
    expected_frames: int,
) -> None:
    input_flags = _ffmpeg_input_flags()
    if SEGMENT_H264_MUX_MODE == "parity":
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *input_flags,
            "-f",
            "h264",
            "-r",
            str(rate),
            "-i",
            str(h264_path),
            "-frames:v",
            str(expected_frames),
            "-c:v",
            "libx264",
            "-preset",
            SEGMENT_H264_REENCODE_PRESET,
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(mp4_path),
        ]
    else:
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *input_flags,
            "-f",
            "h264",
            "-r",
            str(rate),
            "-i",
            str(h264_path),
            "-c",
            "copy",
            str(mp4_path),
        ]
    subprocess.run(cmd, check=True, timeout=300)


def mux_h264_buffers_to_mp4(
    segment_dir: Path,
    streams: dict[str, list[bytes]],
    *,
    fps: int | None = None,
    delete_raw: bool = True,
    expected_rows: int | None = None,
) -> list[Path]:
    """Write concatenated Annex-B once per camera, mux to MP4 with strict frame parity."""
    if not streams:
        return []
    ffmpeg = resolve_ffmpeg()
    out_dir = segment_dir / "streams"
    out_dir.mkdir(parents=True, exist_ok=True)
    rate = fps if fps is not None else SEGMENT_H264_FPS
    chunk_frames = _uniform_chunk_count(streams)
    if expected_rows is not None and expected_rows > 0 and expected_rows != chunk_frames:
        raise RuntimeError(
            f"segment_rows_chunks_mismatch rows={expected_rows} chunks={chunk_frames} "
            f"segment={segment_dir.name}"
        )
    expected_frames = chunk_frames if chunk_frames > 0 else int(expected_rows or 0)
    if expected_frames <= 0:
        raise RuntimeError(f"segment_h264_mux_no_frames segment={segment_dir.name}")

    mp4_paths: list[Path] = []
    for key, chunks in streams.items():
        if not chunks:
            continue
        if len(chunks) != expected_frames:
            raise RuntimeError(
                f"h264_chunk_count_mismatch camera={key} got={len(chunks)} "
                f"expected={expected_frames}"
            )
        safe = key.replace(".", "_")
        h264_path = out_dir / f"{safe}.h264"
        mp4_path = out_dir / f"{safe}.mp4"
        h264_path.write_bytes(b"".join(chunks))
        try:
            _mux_one_camera(
                ffmpeg,
                h264_path,
                mp4_path,
                rate=rate,
                expected_frames=expected_frames,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            print(
                f"segment_h264_mux FAIL camera={key} segment={segment_dir.name} "
                f"mode={SEGMENT_H264_MUX_MODE} frames={expected_frames} "
                f"h264_bytes={h264_path.stat().st_size} err={exc}",
                flush=True,
            )
            raise
        if delete_raw:
            try:
                h264_path.unlink()
            except OSError:
                pass
        mp4_paths.append(mp4_path)

    verify_segment_stream_mp4s(segment_dir, min_mp4=len(mp4_paths) if mp4_paths else SEGMENT_H264_MIN_MP4)
    _verify_segment_mp4_resolution(mp4_paths)
    if SEGMENT_H264_STRICT and expected_frames > 0:
        aligned = enforce_segment_frame_parity(
            segment_dir,
            expected_frames,
            mp4_paths=mp4_paths,
        )
        print(
            f"segment_frame_parity_ok segment={segment_dir.name} frames={aligned} "
            f"mode={SEGMENT_H264_MUX_MODE}",
            flush=True,
        )
    return mp4_paths


def _verify_segment_mp4_resolution(mp4_paths: list[Path]) -> None:
    """Fail fast when H264 MP4 resolution drifts from ISP output (1280x800)."""
    expect_w = int(os.environ.get("OAK_DEFAULT_FRAME_WIDTH", "1280"))
    expect_h = int(os.environ.get("OAK_DEFAULT_FRAME_HEIGHT", "800"))
    if os.environ.get("SEGMENT_H264_STRICT_RES", "1").strip().lower() in ("0", "false", "no"):
        return

    for mp4_path in mp4_paths:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffprobe failed for {mp4_path}: {proc.stderr[-300:]}")
        stream = json.loads(proc.stdout or "{}").get("streams", [{}])[0]
        w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
        if w != expect_w or h != expect_h:
            raise RuntimeError(
                f"segment_mp4_resolution_mismatch path={mp4_path.name} got={w}x{h} "
                f"expected={expect_w}x{expect_h} (EEPROM/ISP); fix OAK setVideoSize on capture host"
            )
