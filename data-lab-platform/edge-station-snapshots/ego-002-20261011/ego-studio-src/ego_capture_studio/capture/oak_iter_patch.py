# Append to oak_4p_capture.py after record_episode method:

    def iter_synced_frames(self, duration_s: float):
        """Yield per synced quad-frame: (timestamp_ns, dict[lerobot_video_key -> rgb_hwc])."""
        if self._device is None:
            raise RuntimeError("Call connect() first")

        from ego_capture_studio.capture.buffers import EpisodeBuffers
        from ego_capture_studio.capture.camera_map import OAK_SOCKET_TO_LEROBOT_VIDEO, PRIMARY_OAK_SOCKET
        from ego_capture_studio.capture.imu_align import imu6_at_timestamp

        buf = EpisodeBuffers()
        last_seen: dict[str, object] = {}
        t_end = time.monotonic() + float(duration_s)

        while time.monotonic() < t_end:
            self._drain_imu(buf)
            got_primary = False
            ts_ns: int | None = None

            for cam_name, queue in self._cam_queues.items():
                pkt = queue.tryGet()
                while pkt is not None:
                    last_seen[cam_name] = pkt.getCvFrame()
                    if cam_name == PRIMARY_OAK_SOCKET:
                        got_primary = True
                        ts_ns = _device_ts_ns(pkt.getTimestampDevice())
                    pkt = queue.tryGet()

            if not got_primary or ts_ns is None:
                continue
            if not all(name in last_seen for name in self._cam_list):
                continue

            frames = {
                OAK_SOCKET_TO_LEROBOT_VIDEO[oak]: _bgr_to_rgb(last_seen[oak])
                for oak in self._cam_list
            }
            g_ts, g, a_ts, a = (
                __import__(
                    "ego_capture_studio.capture.lerobot_episode",
                    fromlist=["_buffers_to_numpy"],
                )._buffers_to_numpy(buf)
            )
            imu6 = imu6_at_timestamp(g_ts, g, a_ts, a, ts_ns)
            yield int(ts_ns), frames, imu6
