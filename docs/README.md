# Data Lab 项目文档

本目录存放 **Data Lab / EGO 采集栈** 运维与架构文档。Label Studio 通用文档见 [labelstud.io/guide](https://labelstud.io/guide/)。

平台部署与日常操作入口：[data-lab-platform/README.md](../data-lab-platform/README.md)

## EGO 采集与上传（ego-lan-214）

| 文档 | 说明 |
|------|------|
| [ego-lan-214-agent-upload-operator-guide.md](./ego-lan-214-agent-upload-operator-guide.md) | 214 Agent 上传操作员手册（商用交付） |
| [ego-lan-214-upload-channel-policy.md](./ego-lan-214-upload-channel-policy.md) | 上传通道策略（Agent vs 浏览器） |
| [ego-lan-214-pipeline-runbook.md](./ego-lan-214-pipeline-runbook.md) | 全流程操作手册 |
| [ego-edge-offline-upload-and-deployment.md](./ego-edge-offline-upload-and-deployment.md) | 离线上传与边缘部署 |
| [ego-lan-214-segment-storage-and-upload.md](./ego-lan-214-segment-storage-and-upload.md) | 段存储与上传数据流 |

## 架构与规划

| 文档 | 说明 |
|------|------|
| [ego-lan-214-p1-data-platform-evolution.md](./ego-lan-214-p1-data-platform-evolution.md) | 数据平台架构路线图 |
| [ego-lan-214-p1-phase1-implementation-plan.md](./ego-lan-214-p1-phase1-implementation-plan.md) | 阶段一实施计划（W1–W2） |
| [ego-lan-214-agent-upload-commercial-prd.md](./ego-lan-214-agent-upload-commercial-prd.md) | Agent 上传商用体验 PRD |
| [ego-lan-214-p0p-mux-deploy.md](./ego-lan-214-p0p-mux-deploy.md) | P0+ mux/scaffold 部署与验收（历史） |

## 数据集

| 文档 | 说明 |
|------|------|
| [ego-dataset-management.md](./ego-dataset-management.md) | EGO 数据集管理（Viewer vs 训练、prune） |

## CI 自动生成

`source/includes/tags/` 由前端 CI 从 Label Studio 标注标签 schema 生成，请勿手工编辑。
