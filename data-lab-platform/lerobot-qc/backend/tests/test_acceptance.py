"""Automatic acceptance check of finished delivery rebuilds (real ffmpeg-encoded videos)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import acceptance_service
import rebuild_service
from dataset_manager import load_local_dataset
from qc_store import QcStore, forget_open_stores, manifest_delivery_status
from test_minimal_rebuild import _prepare_store, minimal_dataset  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

RGB = "observation.images.top"
DEPTH = "observation.images.depth"


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.delenv("LEROBOT_QC_ACCEPTANCE", raising=False)
    forget_open_stores()
    rebuild_service._jobs.clear()
    yield
    forget_open_stores()
    rebuild_service._jobs.clear()


def _encode(dst: Path, *codec_args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5", "-frames:v", "15", *codec_args, str(dst)],
        check=True,
    )


@pytest.fixture()
def video_dataset(minimal_dataset):  # noqa: F811
    """minimal_dataset with real videos: 3 episodes x 5 frames at 5 fps in one shared file each."""
    root, sidecar = minimal_dataset
    info_path = root / "meta/info.json"
    info = json.loads(info_path.read_text())
    info["features"][DEPTH]["info"].update({"video.codec": "ffv1", "video.pix_fmt": "gray16le"})
    info["features"][RGB]["info"].update({"video.codec": "h264", "video.pix_fmt": "yuv420p"})
    info_path.write_text(json.dumps(info))
    _encode(root / f"videos/{RGB}/chunk-000/file-000.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "5")
    _encode(root / f"videos/{DEPTH}/chunk-000/file-000.mkv", "-c:v", "ffv1", "-pix_fmt", "gray16le")
    return root, sidecar


def _rebuild(root: Path, sidecar: Path, out: Path, **kwargs) -> tuple[dict, QcStore, list[dict]]:
    store = _prepare_store(root, sidecar)  # rejects episode 1, overrides episode 0's instruction
    state = load_local_dataset(str(root))
    updates: list[dict] = []
    with patch.object(rebuild_service, "_sync_job", side_effect=lambda _id, _store, **u: updates.append(u)):
        result = rebuild_service._rebuild_minimal_delivery_dataset(
            state=state, store=store, output_root=out, batch_id="b", job_id="job1", **kwargs
        )
    return result, store, updates


def _check(out: Path, root: Path, **kwargs) -> dict:
    return acceptance_service.check_delivery(
        out, root, removed={1}, overrides={"0": "place the cube on the plate"}, **kwargs
    )


def test_rebuild_runs_acceptance_as_last_phase_and_passes(video_dataset, tmp_path: Path) -> None:
    root, sidecar = video_dataset
    out = tmp_path / "delivery"
    result, _, updates = _rebuild(root, sidecar, out)

    acceptance = result["acceptance"]
    assert acceptance["ok"] is True, acceptance["problems"]
    assert acceptance["problems"] == []
    assert acceptance["checked_at"]
    assert acceptance["episodes"] == 2 and acceptance["frames"] == 10
    assert acceptance["video_segments"] == 4  # 2 episodes x (rgb + depth)
    assert acceptance["depth_keys"] == 1
    assert (out / "meta" / "depth_features.json").is_file()

    phases = [u.get("progress") for u in updates]
    assert phases[-1] == "verifying"
    assert phases.index("verifying") > phases.index("recomputing stats")
    assert updates[-1]["progress_current"] == updates[-1]["progress_total"] == 2
    assert not (out / rebuild_service.JOB_MARKER).exists()


def test_acceptance_detects_broken_delivery(video_dataset, tmp_path: Path) -> None:
    root, sidecar = video_dataset
    out = tmp_path / "delivery"
    _rebuild(root, sidecar, out)

    data_path = out / "data/chunk-000/file-000.parquet"
    data = pd.read_parquet(data_path)
    data.loc[data["episode_index"] == 1, "task_index"] = 0  # wrong task text for episode 2 (source)
    data.loc[3, "frame_index"] = 7
    pq.write_table(pa.Table.from_pandas(data, preserve_index=False), data_path)
    stats_path = out / "meta/stats.json"
    stats = json.loads(stats_path.read_text())
    stats["action"]["count"] = [99]
    stats_path.write_text(json.dumps(stats))
    (out / f"videos/{RGB}/chunk-000/file-000.mp4").unlink()

    acceptance = _check(out, root)
    text = "\n".join(acceptance["problems"])
    assert acceptance["ok"] is False
    assert "task text mismatches in 1 episodes (source episodes [2])" in text
    assert "frame_index not contiguous in 1 episodes" in text
    assert "stats.json action count [99] != [10]" in text
    assert f"episode 0 top: missing (videos/{RGB}/chunk-000/file-000.mp4)" in text
    assert "depth" not in text  # depth segments still fine


def test_acceptance_detects_duplicate_episodes(video_dataset, tmp_path: Path) -> None:
    root, sidecar = video_dataset
    out = tmp_path / "delivery"
    _rebuild(root, sidecar, out)
    assert _check(out, root)["duplicate_groups"] == 0

    data_path = out / "data/chunk-000/file-000.parquet"
    data = pd.read_parquet(data_path)
    first = data[data["episode_index"] == 0]
    mask = data["episode_index"] == 1
    for col in ("action", "observation.state"):
        data.loc[mask, col] = pd.Series(first[col].tolist(), index=data.index[mask])
    pq.write_table(pa.Table.from_pandas(data, preserve_index=False), data_path)

    acceptance = _check(out, root)
    assert acceptance["ok"] is False
    assert acceptance["duplicate_groups"] == 1
    assert "1 groups of identical episodes (2 episodes, e.g. [[0, 1]])" in acceptance["problems"]


def test_acceptance_detects_wrong_counts_and_short_video(video_dataset, tmp_path: Path) -> None:
    root, sidecar = video_dataset
    out = tmp_path / "delivery"
    _rebuild(root, sidecar, out)

    # Expect episode 1 to be kept as well -> episode and frame counts are wrong.
    acceptance = acceptance_service.check_delivery(out, root, removed=set(), overrides={"0": "place the cube on the plate"})
    assert any(p.startswith("episodes 2 != expected 3") for p in acceptance["problems"])
    assert any(p.startswith("frames 10 != expected 15") for p in acceptance["problems"])

    # A depth video that decodes as 8-bit and only has 3 frames.
    depth = out / f"videos/{DEPTH}/chunk-000/file-000.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5", "-frames:v", "3",
         "-c:v", "ffv1", "-pix_fmt", "gray", str(depth)],
        check=True,
    )
    acceptance = _check(out, root)
    text = "\n".join(acceptance["problems"])
    assert "decodes as gray, expected 16-bit" in text
    assert "episode 1 depth: 5 frames" in text  # frames 3..4 of episode 0 and all of episode 1 are gone
    assert acceptance["bad_video_segments"] == 2


def test_acceptance_can_be_skipped_via_env(video_dataset, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LEROBOT_QC_ACCEPTANCE", "0")
    root, sidecar = video_dataset
    with patch.object(acceptance_service, "check_delivery") as check:
        result, _, updates = _rebuild(root, sidecar, tmp_path / "delivery")
    check.assert_not_called()
    assert result["acceptance"] is None
    assert "verifying" not in [u.get("progress") for u in updates]


def test_crash_in_check_is_reported_not_raised(tmp_path: Path) -> None:
    acceptance = acceptance_service.check_delivery(tmp_path / "nope", tmp_path, removed=set(), overrides={})
    assert acceptance["ok"] is False
    assert acceptance["problems"][0].startswith("acceptance check crashed: FileNotFoundError")


def test_problem_list_is_capped(tmp_path: Path) -> None:
    problems = acceptance_service._Problems()
    for i in range(acceptance_service.MAX_PROBLEMS + 5):
        problems.add(f"p{i}")
    assert len(problems.items) == acceptance_service.MAX_PROBLEMS and problems.total == acceptance_service.MAX_PROBLEMS + 5


def test_restart_during_verify_resumes_and_verifies_again(video_dataset, tmp_path: Path) -> None:
    root, sidecar = video_dataset
    out = tmp_path / "delivery"
    with patch.object(rebuild_service, "check_delivery", side_effect=KeyboardInterrupt("restart")), pytest.raises(
        KeyboardInterrupt
    ):
        _rebuild(root, sidecar, out)
    assert (out / rebuild_service.JOB_MARKER).is_file()  # still owned by the job -> resumable

    result, _, _ = _rebuild(root, sidecar, out, resume=True)
    assert result["videos_resumed"] == 2  # finished videos are kept
    assert result["acceptance"]["ok"] is True, result["acceptance"]["problems"]
    assert not (out / rebuild_service.JOB_MARKER).exists()


def test_delivery_status_exposes_acceptance(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    acceptance = {"ok": False, "checked_at": "2026-01-01T00:00:00+00:00", "problems": ["stats.json action count"]}
    manifest = {
        "rebuild_jobs": [
            {
                "job_id": "j",
                "status": "completed",
                "output_root": str(out),
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "2026-01-01T00:01:00+00:00",
                "acceptance": acceptance,
            }
        ]
    }
    assert manifest_delivery_status(manifest)["acceptance"] == acceptance
    manifest["rebuild_jobs"][0].pop("acceptance")
    assert manifest_delivery_status(manifest)["acceptance"] is None


def test_decode_video_reports_timestamps_and_pixel_format(tmp_path: Path) -> None:
    path = tmp_path / "d.mkv"
    _encode(path, "-c:v", "ffv1", "-pix_fmt", "gray16le")
    stamps, pix_fmt = acceptance_service.decode_video(path)
    assert pix_fmt == "gray16le"
    assert np.allclose(stamps, np.arange(15) / 5.0)


def test_depth_without_declared_codec_stays_16_bit(minimal_dataset, tmp_path: Path) -> None:  # noqa: F811
    """Source info.json declares no video.codec/pix_fmt (the fixture's default): re-encoding
    must still keep FFV1 gray16le, so acceptance passes."""
    root, sidecar = minimal_dataset
    _encode(root / f"videos/{RGB}/chunk-000/file-000.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "5")
    _encode(root / f"videos/{DEPTH}/chunk-000/file-000.mkv", "-c:v", "ffv1", "-pix_fmt", "gray16le")
    result, _, _ = _rebuild(root, sidecar, tmp_path / "delivery")
    assert result["acceptance"]["ok"] is True, result["acceptance"]["problems"]
    _, pix_fmt = acceptance_service.decode_video(tmp_path / f"delivery/videos/{DEPTH}/chunk-000/file-000.mkv")
    assert pix_fmt == "gray16le"
