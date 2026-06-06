"""OAK-4P-New socket names mapped to LeRobot v3 video feature keys (front/rear layout)."""

from __future__ import annotations

# OAK board socket -> LeRobot observation.images.* (CAM_A stays PRIMARY / 20Hz grid)
OAK_SOCKET_TO_LEROBOT_VIDEO: dict[str, str] = {
    "CAM_A": "observation.images.camera_front_left",
    "CAM_B": "observation.images.camera_front_right",
    "CAM_C": "observation.images.camera_rear_left",
    "CAM_D": "observation.images.camera_rear_right",
}

LEROBOT_VIDEO_TO_OAK_SOCKET: dict[str, str] = {v: k for k, v in OAK_SOCKET_TO_LEROBOT_VIDEO.items()}

PRIMARY_OAK_SOCKET = "CAM_A"
PRIMARY_LEROBOT_VIDEO_KEY = OAK_SOCKET_TO_LEROBOT_VIDEO[PRIMARY_OAK_SOCKET]

ALL_LEROBOT_VIDEO_KEYS: tuple[str, ...] = tuple(OAK_SOCKET_TO_LEROBOT_VIDEO.values())
ALL_OAK_SOCKETS: tuple[str, ...] = tuple(OAK_SOCKET_TO_LEROBOT_VIDEO.keys())

# Read-only aliases for historical datasets / transitional uploads (new writes use ALL_LEROBOT_VIDEO_KEYS).
LEGACY_LEROBOT_VIDEO_KEYS: tuple[str, ...] = (
    "observation.images.camera_head_left",
    "observation.images.camera_head_right",
    "observation.images.camera_depth_head",
    "observation.images.camera_02",
)

LEGACY_TO_LEROBOT_VIDEO: dict[str, str] = {
    "observation.images.camera_head_left": "observation.images.camera_front_left",
    "observation.images.camera_head_right": "observation.images.camera_front_right",
    "observation.images.camera_depth_head": "observation.images.camera_rear_left",
    "observation.images.camera_02": "observation.images.camera_rear_right",
}
