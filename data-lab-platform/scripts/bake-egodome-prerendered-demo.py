#!/usr/bin/env python3
"""Bake egodome viewer overlays (depth + hand skeleton) into LeRobot v3 MP4 videos."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

HAND_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (0, 9), (9, 10), (10, 11), (11, 12),
    (0, 13), (13, 14), (14, 15), (15, 16),
    (0, 17), (17, 18), (18, 19), (19, 20),
]

FINGER_JOINT_COLORS = [
    (255, 255, 255),
    (255, 255, 255), (255, 255, 255), (255, 255, 255), (255, 255, 255),
    (255, 0, 0), (255, 0, 0), (255, 0, 0), (255, 0, 0),
    (0, 200, 0), (0, 200, 0), (0, 200, 0), (0, 200, 0),
    (0, 80, 255), (0, 80, 255), (0, 80, 255), (0, 80, 255),
    (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0),
]

HAND_MASK_COLORS = {
    "left": (246, 130, 59),
    "right": (59, 130, 246),
}

HAND_ID_LABELS = {"left": "ID:1 L", "right": "ID:2 R"}

RENDER = {
    "ALPHA_HIGH": 0.95,
    "ALPHA_LOW": 0.45,
    "ALPHA_REPROJ_CAP": 0.55,
    "REPROJ_THRESHOLD": 20.0,
    "REPROJ_THRESHOLD_LEFT": 96.0,
    "QUALITY_LOW": 2,
}

CAMERA_KEYS = [
    "observation.images.camera_front_left",
    "observation.images.camera_front_right",
    "observation.images.camera_rear_left",
    "observation.images.camera_rear_right",
]


@dataclass
class EpisodeSpec:
    episode_index: int
    episode_key: str
    frame_count: int
    source_mp4: dict[str, Path]
    depth_dir: Path


def is_valid_joint(u: float, v: float) -> bool:
    return math.isfinite(u) and math.isfinite(v) and (u > 1 or v > 1)


def is_valid_skeleton(flat: list[float], min_joints: int = 5) -> bool:
    if not flat or len(flat) < 2 or not is_valid_joint(flat[0], flat[1]):
        return False
    seen = set()
    for j in range(21):
        u = flat[j * 2]
        v = flat[j * 2 + 1]
        if not is_valid_joint(u, v):
            continue
        seen.add(f"{round(u)}:{round(v)}")
        if len(seen) >= min_joints:
            return True
    return False


def reproj_threshold(payload: dict, side: str) -> float:
    if side == "left":
        t = payload.get("reproj_threshold_px_left")
        if isinstance(t, (int, float)) and t > 0:
            return float(t)
        return RENDER["REPROJ_THRESHOLD_LEFT"]
    t = payload.get("reproj_threshold_px_right") or payload.get("reproj_threshold_px")
    if isinstance(t, (int, float)) and t > 0:
        return float(t)
    return RENDER["REPROJ_THRESHOLD"]


def metric_for_hand(hand_payload: dict, payload: dict, row: int, key: str):
    if hand_payload.get(key) and row < len(hand_payload[key]):
        return hand_payload[key][row]
    if payload.get("hands_mode") == "both" and payload.get("hands"):
        return None
    if payload.get(key) and row < len(payload[key]):
        return payload[key][row]
    return None


def should_draw_hand(side: str, frame_quality, reproj_err, payload: dict, hand_confidence) -> bool:
    if hand_confidence is not None and side == "left" and float(hand_confidence) <= 0:
        return False
    if (
        hand_confidence is not None
        and side == "right"
        and float(hand_confidence) > 0
        and float(hand_confidence) < 0.5
    ):
        return False
    threshold = reproj_threshold(payload, side)
    if frame_quality is not None and float(frame_quality) >= RENDER["QUALITY_LOW"]:
        return False
    if reproj_err is not None and float(reproj_err) > threshold:
        return False
    return True


def blend_alpha(frame_quality, reproj_err, threshold: float) -> float:
    alpha = RENDER["ALPHA_HIGH"]
    if frame_quality is not None and float(frame_quality) >= RENDER["QUALITY_LOW"]:
        alpha = RENDER["ALPHA_LOW"]
    if reproj_err is not None and float(reproj_err) > threshold:
        alpha = min(alpha, RENDER["ALPHA_REPROJ_CAP"])
    return alpha


def alpha_for_hand(side: str, hand_payload: dict, row: int, payload: dict) -> float:
    conf = None
    confs = hand_payload.get("hand_confidence")
    if confs and row < len(confs):
        conf = confs[row]
    if side == "left" and conf is not None and float(conf) > 0:
        return max(RENDER["ALPHA_LOW"], min(1.0, float(conf)))
    fq = hand_payload.get("frame_quality")
    re = hand_payload.get("reprojection_error")
    return blend_alpha(
        fq[row] if fq and row < len(fq) else None,
        re[row] if re and row < len(re) else None,
        reproj_threshold(payload, side),
    )


def frame_row_index(payload: dict, frame_idx: int) -> int:
    frames = payload.get("frame_index") or []
    for i, value in enumerate(frames):
        if int(value) == int(frame_idx):
            return i
    return -1


def hands_to_draw(payload: dict, frame_idx: int) -> list[dict]:
    row = frame_row_index(payload, frame_idx)
    out: list[dict] = []
    if payload.get("hands_mode") == "both" and payload.get("hands"):
        for side in ("left", "right"):
            hand_payload = payload["hands"].get(side) or {}
            kp2d = hand_payload.get("kp2d") or []
            if row < 0 or row >= len(kp2d):
                continue
            fq = metric_for_hand(hand_payload, payload, row, "frame_quality")
            re = metric_for_hand(hand_payload, payload, row, "reprojection_error")
            conf = (
                hand_payload.get("hand_confidence", [None] * len(kp2d))[row]
                if hand_payload.get("hand_confidence")
                else None
            )
            if not should_draw_hand(side, fq, re, payload, conf):
                continue
            flat = kp2d[row]
            if not is_valid_skeleton(flat):
                continue
            out.append(
                {
                    "side": side,
                    "flat": flat,
                    "alpha": alpha_for_hand(side, hand_payload, row, payload),
                }
            )
        if out:
            return out
    kp2d = payload.get("kp2d") or []
    if row < 0 or row >= len(kp2d):
        return []
    flat = kp2d[row]
    if not is_valid_skeleton(flat):
        return []
    fq = payload.get("frame_quality")
    re = payload.get("reprojection_error")
    if not should_draw_hand("right", fq[row] if fq else None, re[row] if re else None, payload, None):
        return []
    return [
        {
            "side": "right",
            "flat": flat,
            "alpha": blend_alpha(
                fq[row] if fq else None,
                re[row] if re else None,
                reproj_threshold(payload, "right"),
            ),
        }
    ]


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = [(p[0], p[1]) for p in points if math.isfinite(p[0]) and math.isfinite(p[1])]
    if len(pts) <= 1:
        return pts
    pts.sort(key=lambda p: (p[0], p[1]))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def scale_points(flat: list[float], scale_x: float, scale_y: float) -> list[tuple[float, float, bool]]:
    pts = []
    for j in range(21):
        u = flat[j * 2]
        v = flat[j * 2 + 1]
        pts.append((u * scale_x, v * scale_y, is_valid_joint(u, v)))
    return pts


def draw_rich_hand(frame: np.ndarray, hand: dict, scale_x: float, scale_y: float, panel_scale: float) -> None:
    pts_raw = scale_points(hand["flat"], scale_x, scale_y)
    valid = [(x, y) for x, y, ok in pts_raw if ok]
    hull = convex_hull(valid)
    side = hand["side"]
    alpha = float(hand.get("alpha") or 0.95)
    overlay = frame.copy()
    mask_color = HAND_MASK_COLORS.get(side, HAND_MASK_COLORS["right"])

    if len(hull) >= 3:
        hull_pts = np.array(hull, dtype=np.int32)
        cv2.fillConvexPoly(overlay, hull_pts, mask_color)
        cv2.addWeighted(overlay, 0.35 * alpha, frame, 1.0 - 0.35 * alpha, 0, frame)

    xs = [p[0] for p in valid]
    ys = [p[1] for p in valid]
    if xs and ys:
        pad = max(4, int(min(max(xs) - min(xs), max(ys) - min(ys)) * 0.03))
        x0 = int(min(xs) - pad)
        y0 = int(min(ys) - pad)
        x1 = int(max(xs) + pad)
        y1 = int(max(ys) + pad)
        cv2.rectangle(frame, (x0, y0), (x1, y1), mask_color, max(1, int(2 * panel_scale)), cv2.LINE_AA)

    line_w = max(1, int(round(2.5 * panel_scale)))
    for a_idx, b_idx in HAND_EDGES:
        ax, ay, a_ok = pts_raw[a_idx]
        bx, by, b_ok = pts_raw[b_idx]
        if not a_ok or not b_ok:
            continue
        cv2.line(frame, (int(ax), int(ay)), (int(bx), int(by)), (255, 255, 255), line_w, cv2.LINE_AA)

    radius = max(1, int(round(3.5 * panel_scale)))
    for j, (x, y, ok) in enumerate(pts_raw):
        if not ok:
            continue
        color = FINGER_JOINT_COLORS[j]
        cv2.circle(frame, (int(x), int(y)), radius, color, -1, cv2.LINE_AA)

    if xs and ys:
        label = HAND_ID_LABELS.get(side, side)
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = max(0.35, 0.45 * panel_scale)
        thickness = max(1, int(round(panel_scale)))
        (tw, th), _ = cv2.getTextSize(label, font, scale, thickness)
        lx = int(min(xs))
        ly = max(th + 4, int(min(ys) - 4))
        cv2.rectangle(frame, (lx, ly - th - 4), (lx + tw + 6, ly + 2), mask_color, -1)
        cv2.putText(frame, label, (lx + 3, ly), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)


def bake_hand_overlay_video(
    src_mp4: Path,
    dst_mp4: Path,
    payload: dict,
    fps: float,
    progress_every: int = 500,
) -> None:
    cap = cv2.VideoCapture(str(src_mp4))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {src_mp4}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dst_mp4.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-i",
        str(src_mp4),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(dst_mp4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    src_w = payload.get("width") or width
    src_h = payload.get("height") or height
    scale_x = width / float(src_w) if src_w else 1.0
    scale_y = height / float(src_h) if src_h else 1.0
    panel_scale = max(0.55, min(1.0, min(width, height) / 320.0))

    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        for hand in hands_to_draw(payload, frame_idx):
            draw_rich_hand(frame, hand, scale_x, scale_y, panel_scale)
        proc.stdin.write(frame.tobytes())
        frame_idx += 1
        if frame_idx % progress_every == 0:
            print(f"    hand overlay {frame_idx}/{frame_count}", flush=True)

    cap.release()
    proc.stdin.close()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg encode failed for {dst_mp4} (exit {rc})")


def bake_depth_video(depth_dir: Path, dst_mp4: Path, fps: float, frame_count: int) -> None:
    dst_mp4.parent.mkdir(parents=True, exist_ok=True)
    pattern = str(depth_dir / "frame_%06d.png")
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-start_number",
        "0",
        "-i",
        pattern,
        "-frames:v",
        str(frame_count),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(dst_mp4),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def copy_video(src_mp4: Path, dst_mp4: Path) -> None:
    dst_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src_mp4),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(dst_mp4),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_episode_specs(
    corpus: Path,
    depth_root: Path,
    depth_manifest: dict,
) -> list[EpisodeSpec]:
    episodes_meta = json.loads((corpus / "meta" / "info.json").read_text(encoding="utf-8"))
    total = int(episodes_meta.get("total_episodes") or 0)
    specs: list[EpisodeSpec] = []
    for episode_index in range(total):
        episode_key = f"{episode_index:06d}"
        depth_dir = depth_root / episode_key
        if not depth_dir.is_dir():
            depth_dir = corpus / "offline" / f"episode_{episode_key}" / "depth_preview_front_left"
        depth_entry = (depth_manifest.get("episodes") or {}).get(episode_key) or {}
        frame_count = int(depth_entry.get("frames") or 0)
        source_mp4 = {}
        for cam in CAMERA_KEYS:
            src = corpus / "videos" / cam / "chunk-000" / f"file-{episode_index:03d}.mp4"
            if not src.is_file():
                raise FileNotFoundError(src)
            source_mp4[cam] = src
        if not depth_dir.is_dir():
            raise FileNotFoundError(depth_dir)
        specs.append(
            EpisodeSpec(
                episode_index=episode_index,
                episode_key=episode_key,
                frame_count=frame_count,
                source_mp4=source_mp4,
                depth_dir=depth_dir,
            )
        )
    return specs


def camera_payload(hand_root: dict, episode_key: str) -> dict:
    ep = hand_root["episodes"][episode_key]
    cam = ep["observation.images.camera_front_left"]
    payload = dict(cam)
    payload.setdefault("hands_mode", hand_root.get("hands_mode", "both"))
    payload.setdefault("fps", hand_root.get("fps", 30))
    payload["reproj_threshold_px_left"] = hand_root.get("reproj_threshold_px_left") or RENDER["REPROJ_THRESHOLD_LEFT"]
    payload["reproj_threshold_px_right"] = hand_root.get("reproj_threshold_px_right") or RENDER["REPROJ_THRESHOLD"]
    return payload


def build_dataset(
    corpus: Path,
    hand_json: Path,
    depth_json: Path,
    depth_frames: Path,
    output: Path,
) -> None:
    hand_root = json.loads(hand_json.read_text(encoding="utf-8"))
    depth_manifest = json.loads(depth_json.read_text(encoding="utf-8"))
    fps = float(hand_root.get("fps") or depth_manifest.get("fps") or 30.0)

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    for sub in ("meta", "data"):
        shutil.copytree(corpus / sub, output / sub, dirs_exist_ok=True)

    specs = load_episode_specs(corpus, depth_frames, depth_manifest)
    print(f"==> episodes: {len(specs)}  fps: {fps}")

    for spec in specs:
        print(f"==> episode {spec.episode_index} ({spec.frame_count} frames)")
        payload = camera_payload(hand_root, spec.episode_key)

        front_left_dst = (
            output
            / "videos"
            / "observation.images.camera_front_left"
            / "chunk-000"
            / f"file-{spec.episode_index:03d}.mp4"
        )
        print("  bake front_left + hand overlay")
        bake_hand_overlay_video(
            spec.source_mp4["observation.images.camera_front_left"],
            front_left_dst,
            payload,
            fps,
        )

        front_right_dst = (
            output
            / "videos"
            / "observation.images.camera_front_right"
            / "chunk-000"
            / f"file-{spec.episode_index:03d}.mp4"
        )
        print("  bake front_right depth preview (viewer replaces RGB with depth PNG)")
        bake_depth_video(spec.depth_dir, front_right_dst, fps, spec.frame_count)

        for cam in ("observation.images.camera_rear_left", "observation.images.camera_rear_right"):
            dst = output / "videos" / cam / "chunk-000" / f"file-{spec.episode_index:03d}.mp4"
            print(f"  copy {cam.split('.')[-1]}")
            copy_video(spec.source_mp4[cam], dst)

    manifest = {
        "slug": "egodome-prerendered-demo",
        "source_corpus": str(corpus),
        "description": "LeRobot v3 demo with baked depth (front_right) and hand overlay (front_left)",
        "episodes": len(specs),
        "fps": fps,
        "viewer_equivalent": "http://10.10.10.34:8080/data/egodome with overlays enabled",
    }
    (output / "demo_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"==> done: {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--corpus",
        type=Path,
        default=default_root / "data-storage/corpus/egodome",
    )
    parser.add_argument(
        "--hand-json",
        type=Path,
        default=default_root / "data-storage/samples/egodome_hand_kp2d.json",
    )
    parser.add_argument(
        "--depth-json",
        type=Path,
        default=default_root / "data-storage/samples/egodome_depth_preview.json",
    )
    parser.add_argument(
        "--depth-frames",
        type=Path,
        default=default_root / "data-storage/samples/egodome_depth_preview_frames",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/temp/egodome-prerendered-demo"),
    )
    args = parser.parse_args()
    build_dataset(args.corpus, args.hand_json, args.depth_json, args.depth_frames, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
