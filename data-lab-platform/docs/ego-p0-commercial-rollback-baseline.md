# EGO P0 商业化回撤基线

> **基线版本：** ego-001 v0.1.3 async + MCAP P0–P3  
> **Git 快照：** `d163bcf7d7a7d40d0acac72548d99d5a23050b9e`（P0 优化前）  
> **优化包：** `p0-commercial-v0.1.3`  
> **日期：** 2026-09-04

---

## 1. 回撤范围说明

本次 P0 优化**不重构**异步 derive / disk marker 状态机 / ego-process 主流程，仅在以下触点做兼容式新增：

| 模块 | 基线行为 | P0 变更 |
|------|---------|---------|
| ingest | tar.zst / mcap 可混传同 session | 硬拦截 `PROTOCOL_MIXED` / `PROTOCOL_STATION_LOCKED` |
| 交付契约 | 无 EGO export contract | `ego_export_contract@1.0` |
| 状态视图 | Collection + egodome 双页面对照 | `GET .../delivery-status` 只读 API |
| 交付门禁 | 无 pose_ready 导出拦截 | `ego-export-delivery.py` 硬门禁 + SLO-B |
| 标注 | 仅操作臂 schema | `ego_human_demo_v1` 最小 schema |
| QC | `/qc/` 旁路 | 导出前读取 lerobot-qc sidecar |

---

## 2. 文件级 before-optimize 快照

原始文件副本（单文件回退用）：

```
data-lab-platform/rollback/p0-commercial-v0.1.3/before/
├── receive-tar.mjs
├── receive-mcap.mjs
├── io.mjs
├── ingest-server.mjs
├── index.mjs
├── ego-process
├── ego-pipeline-stations.yaml
└── collection-stations.json
```

---

## 3. P0 新增文件（删除即回退该功能）

| 文件 | 功能 |
|------|------|
| `lerobot-studio/ingest/protocol-guard.mjs` | 双协议拦截 |
| `lerobot-studio/config/station-upload-policy.json` | ego-001 锁定 mcap |
| `lerobot-studio/delivery-status.mjs` | 统一交付状态 API |
| `scripts/ego-export-delivery.py` | 契约化导出 + 门禁 |
| `docs/ego-export-contract.md` | 契约文档 |
| `embodied-annotate/docs/schemas/ego_human_demo_v1.annotation_schema.json` | EGO 标注 schema |

---

## 4. 单文件回退命令

```bash
# 回退单个已改文件（示例：ingest 协议守卫）
cp data-lab-platform/rollback/p0-commercial-v0.1.3/before/receive-tar.mjs \
   data-lab-platform/lerobot-studio/ingest/receive-tar.mjs

# 删除 P0 新增模块（示例：协议守卫）
rm -f data-lab-platform/lerobot-studio/ingest/protocol-guard.mjs \
      data-lab-platform/lerobot-studio/ingest/protocol-guard.test.mjs

# Git 级整包回退（仅 P0 改动未提交时）
git checkout HEAD -- data-lab-platform/lerobot-studio/ingest/receive-tar.mjs
```

---

## 5. 功能开关（无需改代码即可回退行为）

| 环境变量 | 默认 | 回退效果 |
|----------|------|---------|
| `EGO_ALLOW_TARZST_UPLOAD=1` | off | ego-001 允许 tar.zst 上传 |
| `EGO_EXPORT_REQUIRE_QC=0` | on | 导出不要求 QC approved |
| `EGO_CONVERT_SLO_B_SEC=600` | 600 | 放宽 convert 时效 |

---

## 6. 验收基线（回撤后应恢复）

- `ego-upload ego-001` → derive READY → Collection 可见
- `ego-process ego-001` → egodome 可见
- 混传 tar+mcap **不再**被 409 拦截（回退 protocol-guard 后）
