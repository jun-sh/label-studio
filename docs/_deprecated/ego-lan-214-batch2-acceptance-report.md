# ego-lan-214 Batch-2 Acceptance Report

**结论：批次 2（fluent-ffmpeg 视频转码）正式通过验收**

- Date: 2026-07-05
- Station: `ego-lan-214`
- Mux backend: `fluent`（生产默认）
- Extract backend: `python`
- Phase: READY · markers 21 · parquet 6300 · mp4Ok true
- 脚本汇总：pass=22 fail=0 warn=0（主验收）；inject 活测 22/1/3（时序限制，非链路故障）

## MP4 frame counts（与 legacy 基线一致）

| Camera | Frames |
|--------|--------|
| observation.images.camera_front_left | 6301 |
| observation.images.camera_front_right | 6301 |
| observation.images.camera_rear_left | 6301 |
| observation.images.camera_rear_right | 6301 |

## 生产灰度默认

```yaml
DERIVE_MUX_BACKEND=fluent
DERIVE_STANDALONE=1          # 上传/派生进程隔离（商用量产级）
derive-worker                # compose 默认启动，轮询 session.DONE_UPLOAD
```

架构认定：独立 CLI + worker，磁盘状态唯一真相源；in-process 派生可通过 `DERIVE_STANDALONE=0` 回退。

## 参考

- 独立派生运维：`docs/ego-lan-214-standalone-derive-operator.md`
- 批次 3（LeRobot parquet）：`docs/ego-lan-214-batch3-parquet-operator.md`
