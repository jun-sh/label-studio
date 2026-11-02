"""Tests for preview hub camera key aliasing."""

from __future__ import annotations

import unittest

import numpy as np

from preview_hub import PreviewHub


class PreviewHubTests(unittest.TestCase):
    def test_offer_jpegs_maps_depth_left_to_rear_left(self) -> None:
        hub = PreviewHub()
        hub.offer_jpegs(
            {
                "observation.images.camera_front_left": b"fl",
                "observation.images.camera_front_right": b"fr",
                "observation.images.camera_depth_left": b"rl",
                "observation.images.camera_rear_right": b"rr",
            }
        )
        pending = hub.take_pending_jpegs()
        assert pending is not None
        self.assertEqual(pending["front_left"], b"fl")
        self.assertEqual(pending["front_right"], b"fr")
        self.assertEqual(pending["rear_left"], b"rl")
        self.assertEqual(pending["rear_right"], b"rr")
        hub.set_jpeg("rear_left", pending["rear_left"])
        self.assertEqual(hub.get_jpeg("rear_left"), b"rl")

    def test_offer_maps_depth_left_rgb_to_rear_left(self) -> None:
        hub = PreviewHub()
        rgb = np.zeros((2, 2, 3), dtype=np.uint8)
        hub.offer(
            {
                "observation.images.camera_depth_left": rgb,
            }
        )
        pending = hub.take_pending()
        assert pending is not None
        self.assertIs(pending.get("rear_left"), rgb)


if __name__ == "__main__":
    unittest.main()
