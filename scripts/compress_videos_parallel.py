#!/usr/bin/env python3
"""Parallel video compression: trim, scale down, size-controlled output."""

import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Pass-2 defaults: ~15s clips, 640x360, ~2-3MB each => ~300MB total for ~100 videos
MAX_DURATION = 15.0
MAX_SIZE_MB = 5
TARGET_SIZE_MB = 4
VIDEO_KBPS = 1200
MAX_WIDTH = 640
MAX_HEIGHT = 360

COMPRESSED_BASE = Path("/media/user01/My Book/Ego data/compressed")

# Original sources (pass 1)
SOURCES = [
    (
        Path("/media/user01/My Book/Ego data/Ego4D/full_scale_0137"),
        COMPRESSED_BASE / "Ego4D" / "full_scale_0137",
    ),
    (
        Path(
            "/media/user01/My Book/Ego data/EgoDex/assemble_jenga/videos/"
            "observation.images.camera/chunk-000"
        ),
        COMPRESSED_BASE
        / "EgoDex"
        / "assemble_jenga"
        / "videos"
        / "observation.images.camera"
        / "chunk-000",
    ),
    (
        Path("/media/user01/My Book/Ego data/epic_kitchen/P03/videos"),
        COMPRESSED_BASE / "epic_kitchen" / "P03" / "videos",
    ),
]

VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".MP4", ".AVI", ".MKV", ".MOV"}


def probe_video(path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type",
        "-of",
        "json",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    data = json.loads(out)
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return {"duration": duration, "has_audio": has_audio}


def run_ffmpeg(src: Path, dst: Path, trim: float, video_kbps: int, has_audio: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale='min({MAX_WIDTH},iw)':'min({MAX_HEIGHT},ih)':"
        "force_original_aspect_ratio=decrease,format=yuv420p"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-t",
        f"{trim:.3f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-threads",
        "2",
        "-b:v",
        f"{video_kbps}k",
        "-maxrate",
        f"{int(video_kbps * 1.2)}k",
        "-bufsize",
        f"{video_kbps * 2}k",
        "-movflags",
        "+faststart",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "64k"]
    else:
        cmd += ["-an"]
    cmd.append(str(dst))
    subprocess.run(cmd, check=True)


def compress_one(job: tuple[Path, Path, bool]) -> str:
    src, dst, in_place = job
    meta = probe_video(src)
    trim = min(MAX_DURATION, meta["duration"]) if meta["duration"] > 0 else MAX_DURATION
    if trim <= 0:
        return f"FAIL {src.name}: zero duration"

    out = dst.with_suffix(".tmp.mp4") if in_place else dst
    video_kbps = VIDEO_KBPS

    try:
        run_ffmpeg(src, out, trim, video_kbps, meta["has_audio"])
    except subprocess.CalledProcessError as e:
        if out.exists():
            out.unlink(missing_ok=True)
        return f"FAIL {src.name}: ffmpeg error {e}"

    size_mb = out.stat().st_size / (1024 * 1024)
    if size_mb > MAX_SIZE_MB:
        video_kbps = int((TARGET_SIZE_MB * 1024 * 8) / trim) - 64
        video_kbps = max(video_kbps, 400)
        try:
            run_ffmpeg(src, out, trim, video_kbps, meta["has_audio"])
        except subprocess.CalledProcessError as e:
            if out.exists():
                out.unlink(missing_ok=True)
            return f"FAIL {src.name}: re-encode error {e}"
        size_mb = out.stat().st_size / (1024 * 1024)

    if in_place:
        out.replace(dst)
    elif out != dst:
        out.replace(dst)

    return f"OK   {src.name} ({trim:.1f}s, {size_mb:.1f}MB)"


def collect_jobs_from_sources() -> list[tuple[Path, Path, bool]]:
    jobs = []
    for src_root, out_root in SOURCES:
        if not src_root.exists():
            print(f"WARN: source missing: {src_root}", file=sys.stderr)
            continue
        for path in sorted(src_root.rglob("*")):
            if not path.is_file() or path.suffix not in VIDEO_EXTS:
                continue
            rel = path.relative_to(src_root)
            dst = out_root / rel.with_suffix(".mp4")
            jobs.append((path, dst, False))
    return jobs


def collect_jobs_in_place() -> list[tuple[Path, Path, bool]]:
    jobs = []
    for path in sorted(COMPRESSED_BASE.rglob("*.mp4")):
        if path.name.endswith(".tmp.mp4"):
            continue
        jobs.append((path, path, True))
    return jobs


def main() -> int:
    in_place = "--in-place" in sys.argv or "--pass2" in sys.argv
    jobs = collect_jobs_in_place() if in_place else collect_jobs_from_sources()
    if not jobs:
        print("No videos found.")
        return 1

    workers = min(48, max(4, (os.cpu_count() or 8) // 2))
    mode = "in-place recompress" if in_place else "compress from source"
    print(
        f"Processing {len(jobs)} videos ({mode}) with {workers} workers..."
    )
    print(
        f"Settings: {MAX_DURATION}s, {MAX_WIDTH}x{MAX_HEIGHT} max, "
        f"{VIDEO_KBPS}kbps, target total ~几百MB"
    )

    ok = fail = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(compress_one, job): job for job in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            msg = fut.result()
            print(f"[{i}/{len(jobs)}] {msg}")
            if msg.startswith("OK"):
                ok += 1
            else:
                fail += 1

    print(f"\nDone: {ok} ok, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
