# STABLE v0.0.12 — 工作区版本钉扎

**签收日期：** 2026-08-16  
**验收 session：** `sess_40de832c5781475086a76cae64d3a06c`（1417 帧 × 4 相机，100% parity）

## Git 仓库

| 路径 | 分支 | Tag | 说明 |
|------|------|-----|------|
| `workspace/data-lab` | `release/v0.0.12` | `v0.0.12` | 平台 + 130 采集源码（`ego-stream-client`） |
| `workspace/ego-platform` | `main` | `v0.0.12` | pipeline convert / high_freq merge |
| `workspace/ego-hand-pipeline` | — | — | legacy 后端；oak 模式不依赖 |

查看钉扎 commit：

```bash
cd workspace/data-lab && git rev-parse v0.0.12
cd ../ego-platform && git rev-parse v0.0.12
```

## 130 采集站（无独立 git）

130 代码 **由 data-lab 同步**，不以 130 磁盘为 source of truth：

```bash
data-lab-platform/scripts/ego-130-provision.sh server@10.10.10.130 ego-001
```

同步目标：`/home/server/workspace/ego-studio/src/ego_capture_studio/capture/`  
验证：`systemctl --user show ecs-record-oak-stream | grep OAK_H264_SEQUENTIAL`

## 34 平台

```bash
docker build -t data-lab-lerobot-studio:v0.0.12 data-lab-platform/lerobot-studio
RC_STATION=ego-001 bash data-lab-platform/scripts/ci-ego-platform.sh
```

## 一键工作区快照（可选离线备份）

```bash
bash data-lab-platform/scripts/ego-workspace-snapshot.sh v0.0.12
```

输出：`data-storage/backups/ego-workspace-v0.0.12-<date>.tar.zst`

## 从稳定版恢复

```bash
cd workspace/data-lab && git checkout v0.0.12
cd ../ego-platform && git checkout v0.0.12
data-lab-platform/scripts/ego-130-provision.sh server@10.10.10.130 ego-001
```
