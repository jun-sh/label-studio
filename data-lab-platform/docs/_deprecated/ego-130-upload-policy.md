# ego-130 上传策略：生产 vs 调试

## 原则

| 环境 | 上传方式 | 后台 loop |
|------|----------|-----------|
| **生产** | 操作员手动触发 | **禁止** `upload_segments_loop` / `ecs-upload-segments-loop` |
| **测试 / RC / 联调** | 可手动，也可自动 dequeue | **允许** loop（仅验收与调试） |

采集（`ecs-record-oak-stream`）与上传 **解耦**：手机 UI 仅 start/stop 采集，**无上传按钮**（后续可加）。

## 生产：手动上传

关段后，操作员在 130 上执行：

```bash
ego-upload ego-001
```

（`ego-130-provision.sh` 会安装到 `~/.local/bin/ego-upload`。）

等价底层命令：

```bash
set -a; source ~/.config/ego-station.env 2>/dev/null || true; set +a
export PYTHONPATH=/home/server/workspace/ego-studio/src

/home/server/workspace/ego-studio/.venv/bin/python -m ego_capture_studio.cli.upload_segments \
  --limit 0 \
  --ensure-session \
  --segment-root /home/server/cache/ego-001/segments \
  --upload-url http://10.10.10.34:8080/lerobot/api/collection/stations/ego-001/upload
```

34 侧对应：`ego-process ego-001`（derive + convert + 轻量 Viewer sync）。

可选：上传后自动通知 34 排队处理：

```bash
ego-upload ego-001
# 默认通知 34 排队 ego-process；仅上传：ego-upload ego-001 --no-notify
```

- `ego_upload` / 逐帧 multipart：**已废弃**，勿用。
- 协议：`UPLOAD_PROTOCOL=tarzst`（manifest + rows + imu + `streams/*.mp4`；**无** `frames/*.bin`）。

## 切换模式

在 130 上以 `server` 用户运行（仓库脚本可 scp 后执行）：

```bash
# 生产（默认）：停 loop、mask 单元、写入 EGO_UPLOAD_MODE=production
bash data-lab-platform/scripts/ego-130-upload-mode.sh production

# 调试 / RC：启用 ecs-upload-segments-loop + EGO_UPLOAD_MODE=debug
bash data-lab-platform/scripts/ego-130-upload-mode.sh debug

# 查看当前模式与 loop 状态
bash data-lab-platform/scripts/ego-130-upload-mode.sh status
```

### systemd drop-in

| 文件 | 用途 |
|------|------|
| `ecs-upload-segments-loop.service.d/production-manual-only.conf` | 生产默认（loop 立即退出） |
| `ecs-upload-segments-loop.service.d/debug-auto-upload.conf` | 调试时替换为 debug |

`upload_segments_loop.py` 在 `EGO_UPLOAD_MODE != debug` 时 **不会** 上传任何段（即使被 nohup 误启）。

### stack target

- `ecs-oak-capture-stack.target` — **仅采集**，不含上传（Scheme A 生产）。
- `ecs-oak-upload-stack.target` — 上传栈；**仅调试**时 `Wants` loop，生产勿 enable。

## 34 侧验收（不影响生产策略）

```bash
data-lab-platform/scripts/rc-acceptance.sh
RC_STATION=ego-001 RC_UPLOAD_LIMIT=3 data-lab-platform/scripts/rc-e2e-upload.sh   # 断言 READY
```

运维手册：[ego-001-runbook.md](ego-001-runbook.md) · [LeRobot vendor 扩展迁移](ego-lerobot-vendor-ext-migration.md)

RC 脚本走 **手动** `upload_segments`，与生产路径一致。

## 回滚调试 loop

```bash
bash data-lab-platform/scripts/ego-130-upload-mode.sh debug
systemctl --user status ecs-upload-segments-loop.service
```
