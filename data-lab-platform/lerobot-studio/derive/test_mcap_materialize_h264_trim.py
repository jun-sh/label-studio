"""Tests for MCAP materialize H.264 trim-to-IDR (P1a.1 derive-side)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_DERIVE_DIR = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("mcap_materialize", _DERIVE_DIR / "mcap-materialize.py")
_mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules["mcap_materialize_trim"] = _mod
_SPEC.loader.exec_module(_mod)

trim_h264_camera_streams = _mod.trim_h264_camera_streams
materialize_mcap_archive = _mod.materialize_mcap_archive
_first_idr_packet_index = _mod._first_idr_packet_index


def _fake_nal(ntype: int) -> bytes:
    return b"\x00\x00\x00\x01" + bytes([ntype & 0x1F]) + b"\xab\xcd"


def _idr_packet() -> bytes:
    return _fake_nal(7) + _fake_nal(8) + _fake_nal(5)


def _p_packet() -> bytes:
    return _fake_nal(1)


def test_first_idr_packet_index() -> None:
    packets = [(i, _p_packet() if i < 2 else _idr_packet()) for i in range(4)]
    assert _first_idr_packet_index(packets) == 2


def test_trim_h264_aligns_rows_and_streams() -> None:
    camera_h264 = {
        "front_left": [(0, _idr_packet()), (1, _p_packet()), (2, _p_packet())],
        "front_right": [(0, _p_packet()), (1, _idr_packet()), (2, _p_packet())],
        "rear_left": [(0, _idr_packet()), (1, _p_packet()), (2, _p_packet())],
        "rear_right": [(0, _p_packet()), (1, _idr_packet()), (2, _p_packet())],
    }
    rows = [
        {"frame_index": i, "timestamp_ns": (i + 1) * 33_333_333, "observation.state": [0.0] * 6}
        for i in range(3)
    ]
    streams, out_rows, meta = trim_h264_camera_streams(camera_h264, rows)
    assert meta["trim_align_skip_rows"] == 1
    assert meta["trim_content_packet_index"] == 1
    assert meta["frame_count_after_trim"] == 2
    assert len(out_rows) == 2
    assert out_rows[0]["frame_index"] == 0
    assert out_rows[1]["frame_index"] == 1
    warmup = meta.get("decode_warmup_packets") or {}
    for cam in streams:
        assert len(streams[cam]) == warmup.get(cam, 0) + 2
        assert _mod.contains_idr(streams[cam][warmup.get(cam, 0)][1])
    # Both cameras must map output frame 0 to the same source packet index.
    assert camera_h264["front_left"][1][1] in streams["front_left"][0][1]
    assert camera_h264["front_right"][1][1] in streams["front_right"][0][1]


def test_materialize_h264_mcap_trims_leading_p_frames(tmp_path: Path) -> None:
    from mcap.writer import Writer

    ego_client = Path(__file__).resolve().parents[2] / "ego-stream-client"
    sys.path.insert(0, str(ego_client))
    from mcap_segment_writer import (  # noqa: E402
        CAMERA_TOPICS,
        TOPIC_SESSION_META,
        _compressed_video_payload,
    )

    TOPIC_OBS_STATE = "/ego/observation/state"

    mcap_path = tmp_path / "trim.mcap"
    with open(mcap_path, "wb") as fp:
        writer = Writer(fp)
        writer.start()
        schema_id = writer.register_schema(name="ego.SessionMeta", encoding="jsonschema", data=b"{}")
        state_schema = writer.register_schema(name="ego.ObservationState", encoding="jsonschema", data=b"{}")
        cam_schema = writer.register_schema(
            name="foxglove.CompressedVideo",
            encoding="jsonschema",
            data=b"{}",
        )
        meta_ch = writer.register_channel(
            topic=TOPIC_SESSION_META,
            message_encoding="json",
            schema_id=schema_id,
        )
        state_ch = writer.register_channel(
            topic=TOPIC_OBS_STATE,
            message_encoding="json",
            schema_id=state_schema,
        )
        cam_channels = {}
        for topic in CAMERA_TOPICS.values():
            cam_channels[topic] = writer.register_channel(
                topic=topic,
                message_encoding="json",
                schema_id=cam_schema,
            )
        writer.add_message(
            channel_id=meta_ch,
            log_time=0,
            publish_time=0,
            data=json.dumps(
                {
                    "session_id": "sess_trim",
                    "segment_id": "seg_000001",
                    "video_codec": "h264",
                    "task": "trim-test",
                    "frame_count": 3,
                }
            ).encode(),
        )
        per_cam_packets = {
            "front_left": [_idr_packet(), _p_packet(), _p_packet()],
            "front_right": [_p_packet(), _idr_packet(), _p_packet()],
            "rear_left": [_idr_packet(), _p_packet(), _p_packet()],
            "rear_right": [_p_packet(), _idr_packet(), _p_packet()],
        }
        for frame_idx in range(3):
            ts = (frame_idx + 1) * 33_333_333
            writer.add_message(
                channel_id=state_ch,
                log_time=ts,
                publish_time=ts,
                data=json.dumps(
                    {
                        "frame_index": frame_idx,
                        "timestamp_ns": ts,
                        "observation.state": [0.0] * 6,
                    }
                ).encode(),
            )
            for topic, channel_id in cam_channels.items():
                cam_key = topic.rsplit("/", 1)[-1]
                writer.add_message(
                    channel_id=channel_id,
                    log_time=ts,
                    publish_time=ts,
                    data=_compressed_video_payload(
                        timestamp_ns=ts,
                        frame_id=cam_key,
                        h264=per_cam_packets[cam_key][frame_idx],
                    ),
                )
        writer.finish()

    extract_dir = tmp_path / "extract"
    result = materialize_mcap_archive(mcap_path, extract_dir)
    assert result["ok"] is True
    assert result["frame_count"] == 2
    assert result["h264_trim"]["trim_align_skip_rows"] == 1
    rows = [
        json.loads(line)
        for line in (extract_dir / "rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    for cam_key in CAMERA_TOPICS:
        stream = extract_dir / "streams" / f"{cam_key}.h264"
        assert stream.is_file()
        assert _mod.contains_idr(stream.read_bytes()[:64])
