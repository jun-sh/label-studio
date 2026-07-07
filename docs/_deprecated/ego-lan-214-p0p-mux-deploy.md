# ego-lan-214 P0+ mux/scaffold 部署与验收

**范围**：仅 34 `stream-ingest` + `sync-stream-parquet.py`。不动 214、不动 tar.zst、不动上传入口。

## 代码改动明细

| 文件 | 改动 |
|------|------|
| `lerobot-studio/stream-ingest.mjs` | 清除 scaffold 时删除 ≤1 帧占位 MP4；mux 前删除占位文件；仅对真实 MP4 concat；按实际 staging 帧序编码（无 gap hold）；四路串行 mux；四路全部 verify 后统一 purge staging；`info.total_frames` = jsonl 行数 |
| `lerobot-studio/scripts/sync-stream-parquet.py` | parquet 仅写 jsonl 实有行；`timestamp` = 行序号/fps（与 6300 帧视频对齐）；禁止 dense pad 到 max frame_index |
| `scripts/ego-lan-214-reimport-verify.sh` | 默认期望 6300 帧（seg_013–033）；校验 parquet=jsonl |

## 部署步骤（34）

```bash
cd /path/to/data-lab

# 1. 重启 stream-ingest 加载新代码
docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
  restart stream-ingest lerobot nginx

# 2. 仅清 stream（不碰 214 ready/）
EGO_RESET_YES=1 bash data-lab-platform/scripts/ego-lan-214-reset-stream-only.sh --yes

# 3. 214 Agent 上传 ready/ 下 21 个 seg_000013–seg_000033.tar.zst
#    systemctl --user start ecs-oak-upload-stack.target
#    （历史 P0+ 曾用浏览器拖传验收；W2 起改 Agent 通道，见 upload-channel-policy.md）

# 4. 全部成功后等待 2–3 分钟（mux + parquet）

# 5. 验收
bash data-lab-platform/scripts/ego-lan-214-reimport-verify.sh
```

## 验收标准

| 检查项 | 期望 |
|--------|------|
| `.done` 数量 | 21 |
| jsonl 行数 | 6300 |
| parquet 行数 | 6300（= jsonl） |
| 四路 MP4 ffprobe 帧数 | 各 6300 |
| API `totalFrames` | 6300 |
| `chunks.json` 视频 | `finished` 且 frames=6300 |
| 采集页回放 | 0:00 播放四窗格同步；拖到末尾无卡死 |
| 日志 | 无新增 `mux_fail`；有 `mux_staging_purged` |

## 已知数据形态（本次）

- 仅 seg_013–033 → frame_index 3360–9659，**行数 6300**
- 验收脚本 `EXPECTED_FRAMES=6300`（非 9660）
