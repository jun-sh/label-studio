# Legacy ego scripts (archived)

已归档、**勿用于 ego-001 日常生产** 的脚本。

## 当前生产（ego-001）

- **手册：** [ego-001-使用手册.md](../docs/ego-001-使用手册.md)
- **130 上传：** `ego-upload ego-001`（默认含 notify）
- **34 处理：** watcher 自动 `ego-process`，或手动 `ego-process ego-001`
- **验收：** `rc-ego-001-production.sh`
- **部署：** `deploy-stream-ingest-v0.1.1-async.sh`

## 本目录内容

| 类型 | 示例 |
|------|------|
| Phase 1–7 RC | `rc-ego-001-phase*.sh` |
| v0.0.13 / grayscale | `rc-ego-001-fix2-*`, `rc-ego-001-p0-*` |
| H264 / ego-lan-214 | `ego-130-verify-h264*`, `ego-130-h264-*` |
| 旧 deploy | `deploy-stream-ingest-v0.0.*.sh` |
| 实验 pipeline | `ego-run-pipeline-hamer-bf-test`, `ego-run-pipeline-standard` |

**Archived compose：** `data-lab-platform/deploy/archive/compose/`
