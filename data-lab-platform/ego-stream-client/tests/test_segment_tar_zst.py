"""Tests for segment tar.zst pack/unpack."""

from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from segment_tar_zst import pack_segment_tar_zst, sha256_file

try:
    import zstandard as zstd
except ImportError:
    zstd = None


@pytest.fixture
def mini_segment(tmp_path: Path) -> Path:
    seg = tmp_path / "seg_000001"
    (seg / "frames").mkdir(parents=True)
    manifest = {
        "session_id": "sess_test",
        "segment_id": "seg_000001",
        "segment_seq": 1,
        "closed": True,
        "uploaded": False,
        "start_frame_index": 0,
        "end_frame_index": 1,
    }
    (seg / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    rows = [
        {"frame_index": 0, "timestamp_ns": 1, "task": "t", "observation.state": [0] * 6},
        {"frame_index": 1, "timestamp_ns": 2, "task": "t", "observation.state": [0] * 6},
    ]
    (seg / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (seg / "frames" / "00000000.bin").write_bytes(b"DLB1\x01\x04" + b"\x00" * 20)
    (seg / "frames" / "00000001.bin").write_bytes(b"DLB1\x01\x04" + b"\x00" * 20)
    return seg


@pytest.mark.skipif(zstd is None, reason="zstandard not installed")
def test_pack_segment_tar_zst_roundtrip(tmp_path: Path, mini_segment: Path) -> None:
    out = tmp_path / "seg.tar.zst"
    path, digest = pack_segment_tar_zst(mini_segment, out)
    assert path == out
    assert out.is_file()
    assert sha256_file(out) == digest
    assert out.stat().st_size > 0

    extract = tmp_path / "extract"
    extract.mkdir()
    raw = zstd.ZstdDecompressor().decompress(out.read_bytes())
    tar_path = tmp_path / "seg.tar"
    tar_path.write_bytes(raw)
    with tarfile.open(tar_path, "r") as tar:
        tar.extractall(extract, filter="data")
    assert (extract / "manifest.json").is_file()
    assert (extract / "rows.jsonl").is_file()
    assert (extract / "frames" / "00000000.bin").is_file()


def test_pack_requires_zstd_or_cli(tmp_path: Path, mini_segment: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "seg.tar.zst"
    import segment_tar_zst as mod

    monkeypatch.setattr(mod, "zstd", None)
    proc = subprocess.run(["which", "zstd"], capture_output=True)
    if proc.returncode != 0:
        with pytest.raises(RuntimeError, match="zstandard"):
            mod.pack_segment_tar_zst(mini_segment, out)
    else:
        path, digest = mod.pack_segment_tar_zst(mini_segment, out)
        assert path.is_file()
        assert digest
