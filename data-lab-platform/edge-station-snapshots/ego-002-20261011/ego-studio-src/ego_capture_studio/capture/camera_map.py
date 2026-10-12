"""OAK-4P socket names mapped to LeRobot v3 video feature keys via fleet topology."""

from __future__ import annotations

try:
    from .topology import (
        all_lerobot_video_keys,
        depth_oak_socket,
        head_right_oak_socket,
        lerobot_video_to_oak_socket,
        oak_socket_to_lerobot_video,
        primary_lerobot_video_key,
        primary_oak_socket,
        strict_causal_oak_sockets,
        topology_snapshot,
    )
except ImportError:
    from topology import (
        all_lerobot_video_keys,
        depth_oak_socket,
        head_right_oak_socket,
        lerobot_video_to_oak_socket,
        oak_socket_to_lerobot_video,
        primary_lerobot_video_key,
        primary_oak_socket,
        strict_causal_oak_sockets,
        topology_snapshot,
    )

OAK_SOCKET_TO_LEROBOT_VIDEO: dict[str, str] = oak_socket_to_lerobot_video()
LEROBOT_VIDEO_TO_OAK_SOCKET: dict[str, str] = lerobot_video_to_oak_socket()

PRIMARY_OAK_SOCKET = primary_oak_socket()
PRIMARY_LEROBOT_VIDEO_KEY = primary_lerobot_video_key()

ALL_LEROBOT_VIDEO_KEYS: tuple[str, ...] = all_lerobot_video_keys()
ALL_OAK_SOCKETS: tuple[str, ...] = tuple(OAK_SOCKET_TO_LEROBOT_VIDEO.keys())

DEPTH_OAK_SOCKET = depth_oak_socket()
HEAD_RIGHT_OAK_SOCKET = head_right_oak_socket()
STRICT_CAUSAL_OAK_SOCKETS = strict_causal_oak_sockets()

# Read-only aliases for historical datasets / transitional uploads.
LEGACY_LEROBOT_VIDEO_KEYS: tuple[str, ...] = (
    "observation.images.camera_head_left",
    "observation.images.camera_head_right",
    "observation.images.camera_depth_head",
    "observation.images.camera_02",
    "observation.images.camera_rear_left",
)

LEGACY_TO_LEROBOT_VIDEO: dict[str, str] = {
    "observation.images.camera_head_left": "observation.images.camera_front_left",
    "observation.images.camera_head_right": "observation.images.camera_front_right",
    "observation.images.camera_depth_head": "observation.images.camera_depth_left",
    "observation.images.camera_02": "observation.images.camera_rear_right",
    "observation.images.camera_rear_left": "observation.images.camera_depth_left",
}

__all__ = [
    "ALL_LEROBOT_VIDEO_KEYS",
    "ALL_OAK_SOCKETS",
    "DEPTH_OAK_SOCKET",
    "HEAD_RIGHT_OAK_SOCKET",
    "LEGACY_LEROBOT_VIDEO_KEYS",
    "LEGACY_TO_LEROBOT_VIDEO",
    "LEROBOT_VIDEO_TO_OAK_SOCKET",
    "OAK_SOCKET_TO_LEROBOT_VIDEO",
    "PRIMARY_LEROBOT_VIDEO_KEY",
    "PRIMARY_OAK_SOCKET",
    "STRICT_CAUSAL_OAK_SOCKETS",
    "topology_snapshot",
]
