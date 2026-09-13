"""Reference capture defaults for ego-001 (apply to ego_capture_studio.capture.ego_spec on edge host)."""

OAK_CAPTURE_RES_KEY = "800"
OAK_CAPTURE_FPS = 30
OAK_CAPTURE_IMU_HZ = 200
# Product deliverable: 720p (1280×720). ISP emits 1280×800 before center-crop on device.
OAK_ISP_FRAME_WIDTH = 1280
OAK_ISP_FRAME_HEIGHT = 800
OAK_DEFAULT_FRAME_HEIGHT = 720
OAK_DEFAULT_FRAME_WIDTH = 1280
