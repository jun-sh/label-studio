# ego-lan-214 采集标准（EgoVerse 30/200 · 永久生效）

**唯一标准**：RGB **30Hz** + IMU **200Hz** + **1280×800**  
**站点**：10.10.10.214 · `ego-lan-214`  
**禁止**：任何 20Hz 配置、数据、回滚路径

---

## 生产配置

| 项 | 值 |
|---|---|
| systemd drop-in | `z-production-egoverse.conf` |
| CLI | `--fps 30 --imu-hz 200` |
| 时序网格 | `EGO_FRAME_INTERVAL_MS=33` |
| manifest | `sync_mode: egoverse_30hz` |
| 分段 | 1800 帧 / 段（60s @30Hz）；`EGO_SEGMENT_MAX_SECONDS=75` |
| 34 mux | `STREAM_MUX_FPS=30` |

参考：`data-lab-platform/ego-stream-client/config/egoverse_30hz_production.env`

---

## 启停采集

```bash
systemctl --user start ecs-oak-capture-stack.target
systemctl --user stop ecs-oak-capture-stack.target
journalctl --user -u ecs-record-oak-stream -f
```

期望日志：`sync_mode=egoverse_30hz fps_target=30 imu_hz=200`

---

## 导出 / 上传 / 派生

```bash
ego-export
ego-upload
ego-derive-status --watch
EXPECTED_FPS=30 CHECK_DERIVE_ASYNC=1 \
  bash data-lab-platform/scripts/ego-lan-214-reimport-verify.sh
```

---

## Phase0 全量切换

```bash
EGO_RESET_YES=1 bash data-lab-platform/scripts/ego-lan-214-phase0-egoverse-deploy.sh
```

---

## CI 门禁

```bash
bash data-lab-platform/scripts/check-no-20hz.sh
```
