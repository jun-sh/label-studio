import json
from pathlib import Path

from depth_split import DEPTH_SIDECAR, DEPTH_SIDECAR_FORMAT, is_depth_feature, split_depth_features

RGB = {
    "dtype": "video",
    "shape": [3, 4, 4],
    "info": {"video.codec": "h264", "video.is_depth_map": False},
}
DEPTH = {
    "dtype": "video",
    "shape": [1, 4, 4],
    "info": {
        "video.codec": "ffv1",
        "video.pix_fmt": "gray16le",
        "video.is_depth_map": True,
        "depth.video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mkv",
    },
}
VIDEO_PATH = "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"


def _make(root: Path, features: dict, stats: dict | None = None) -> Path:
    (root / "meta").mkdir(parents=True)
    info = {"fps": 5, "video_path": VIDEO_PATH, "features": features}
    (root / "meta" / "info.json").write_text(json.dumps(info))
    if stats is not None:
        (root / "meta" / "stats.json").write_text(json.dumps(stats))
    return root


def _read(root: Path, name: str) -> dict:
    return json.loads((root / "meta" / name).read_text())


def test_is_depth_feature():
    assert is_depth_feature(DEPTH)
    assert not is_depth_feature(RGB)
    assert not is_depth_feature({"dtype": "float32", "shape": [7]})
    # Converter convention without the flag still counts as depth.
    assert is_depth_feature({"dtype": "video", "info": {"depth.video_path": "x.mkv"}})


def test_split_moves_depth_features_and_stats(tmp_path):
    root = _make(
        tmp_path / "ds",
        {"observation.images.rgb": RGB, "observation.images.depth": DEPTH, "action": {"dtype": "float32", "shape": [7]}},
        {"observation.images.rgb": {"count": [10]}, "observation.images.depth": {"count": [10]}, "action": {"count": [10]}},
    )

    moved = split_depth_features(root)

    assert moved == ["observation.images.depth"]
    info = _read(root, "info.json")
    assert set(info["features"]) == {"observation.images.rgb", "action"}
    assert info["video_path"] == VIDEO_PATH
    assert set(_read(root, "stats.json")) == {"observation.images.rgb", "action"}
    sidecar = _read(root, DEPTH_SIDECAR)
    assert sidecar["format"] == DEPTH_SIDECAR_FORMAT
    assert sidecar["fps"] == 5
    assert sidecar["features"] == {"observation.images.depth": DEPTH}
    assert sidecar["stats"] == {"observation.images.depth": {"count": [10]}}
    assert sidecar["video_paths"]["observation.images.depth"].endswith(".mkv")


def test_split_is_idempotent(tmp_path):
    root = _make(tmp_path / "ds", {"observation.images.depth": DEPTH, "observation.images.rgb": RGB}, {})
    split_depth_features(root)
    first = _read(root, DEPTH_SIDECAR)

    assert split_depth_features(root) == []
    assert _read(root, DEPTH_SIDECAR) == first
    assert set(_read(root, "info.json")["features"]) == {"observation.images.rgb"}


def test_split_without_depth_is_noop(tmp_path):
    root = _make(tmp_path / "ds", {"observation.images.rgb": RGB}, {"observation.images.rgb": {"count": [1]}})
    before = (root / "meta" / "info.json").read_text()

    assert split_depth_features(root) == []
    assert (root / "meta" / "info.json").read_text() == before
    assert not (root / "meta" / DEPTH_SIDECAR).exists()


def test_split_derives_mkv_path_when_converter_path_missing(tmp_path):
    depth = {"dtype": "video", "shape": [1, 4, 4], "info": {"video.is_depth_map": True}}
    root = _make(tmp_path / "ds", {"observation.images.depth": depth})

    split_depth_features(root)

    assert _read(root, DEPTH_SIDECAR)["video_paths"]["observation.images.depth"] == VIDEO_PATH.replace(".mp4", ".mkv")
    assert not (root / "meta" / "stats.json").exists()
