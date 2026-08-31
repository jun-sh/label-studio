"""Regression: frame_count inferred from camera topics when session_meta omits it."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "validate_mcap_archive",
    Path(__file__).with_name("validate-mcap-archive.py"),
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
validate_mcap_archive = _mod.validate_mcap_archive

pytest.importorskip("mcap")


def _write_minimal_mcap(path: Path, *, frame_count_meta: int | None, cam_frames: int) -> None:
    from mcap.writer import Writer

    with path.open("wb") as fp:
        writer = Writer(fp)
        writer.start()
        meta_schema = writer.register_schema(name="ego.SessionMeta", encoding="jsonschema", data=b"{}")
        meta_ch = writer.register_channel(
            topic="/ego/session_meta",
            message_encoding="json",
            schema_id=meta_schema,
        )
        img_schema = writer.register_schema(name="foxglove.CompressedImage", encoding="jsonschema", data=b"{}")
        payload = {
            "schema_version": 1,
            "session_id": "sess_test",
            "segment_id": "seg_000001",
            "station_id": "ego-mcap-pilot",
        }
        if frame_count_meta is not None:
            payload["frame_count"] = frame_count_meta
        writer.add_message(
            channel_id=meta_ch,
            log_time=0,
            publish_time=0,
            data=json.dumps(payload).encode("utf-8"),
        )
        for topic in (
            "/ego/camera/front_left",
            "/ego/camera/front_right",
            "/ego/camera/rear_left",
            "/ego/camera/rear_right",
        ):
            ch = writer.register_channel(topic=topic, message_encoding="json", schema_id=img_schema)
            for i in range(cam_frames):
                ts = (i + 1) * 33_333_333
                writer.add_message(
                    channel_id=ch,
                    log_time=ts,
                    publish_time=ts,
                    data=json.dumps({"data": "e30=", "format": "jpeg"}).encode("utf-8"),
                )
        imu_schema = writer.register_schema(name="ego.ImuRaw", encoding="jsonschema", data=b"{}")
        imu_ch = writer.register_channel(topic="/ego/imu/raw", message_encoding="json", schema_id=imu_schema)
        writer.add_message(
            channel_id=imu_ch,
            log_time=33_333_333,
            publish_time=33_333_333,
            data=json.dumps({"sensor": "gyro", "x": 0, "y": 0, "z": 0}).encode("utf-8"),
        )
        writer.finish()


def test_frame_count_from_camera_topics_when_meta_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mcap = Path(tmp) / "seg.mcap"
        _write_minimal_mcap(mcap, frame_count_meta=None, cam_frames=5)
        result = validate_mcap_archive(mcap)
        assert result["ok"] is True
        assert result["frame_count"] == 5


def test_frame_count_from_meta_when_present() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mcap = Path(tmp) / "seg.mcap"
        _write_minimal_mcap(mcap, frame_count_meta=3, cam_frames=3)
        result = validate_mcap_archive(mcap)
        assert result["ok"] is True
        assert result["frame_count"] == 3


if __name__ == "__main__":
    test_frame_count_from_camera_topics_when_meta_missing()
    test_frame_count_from_meta_when_present()
    print("OK")
