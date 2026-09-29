# Data Lab 项目文档

本目录存放 **Data Lab / EGO 采集栈** 运维与架构文档。Label Studio 通用文档见 [labelstud.io/guide](https://labelstud.io/guide/)。

平台部署与日常操作入口：[data-lab-platform/README.md](../data-lab-platform/README.md)

## EGO 采集与上传（ego-001）

生产采集站为 **ego-001**（`10.10.10.130`）。日常运维见 [data-lab-platform/docs/gpd-micropc2-capture-station-setup.md](../data-lab-platform/docs/gpd-micropc2-capture-station-setup.md)。

旧版 **ego-lan-214**（`10.10.10.214`）文档已退役，见 [_deprecated/](./_deprecated/README.md)。

## 架构与规划（历史）

| 文档 | 说明 |
|------|------|
| [data-lab-platform/docs/ego-maxinsights-pipeline.md](../data-lab-platform/docs/ego-maxinsights-pipeline.md) | **MaxInsights 对齐**：9 感知模块 + 生产闭环 + taxonomy |
| [data-lab-platform/docs/ego-core-pipeline.md](../data-lab-platform/docs/ego-core-pipeline.md) | EGO 采集→上传→派生最简主路径 |
| [_deprecated/ego-lan-214-p1-data-platform-evolution.md](./_deprecated/ego-lan-214-p1-data-platform-evolution.md) | 数据平台架构路线图（历史） |
| [_deprecated/ego-lan-214-p1-phase1-implementation-plan.md](./_deprecated/ego-lan-214-p1-phase1-implementation-plan.md) | 阶段一实施计划（历史） |

## 数据集

| 文档 | 说明 |
|------|------|
| [ego-dataset-management.md](./ego-dataset-management.md) | EGO 数据集管理（Viewer vs 训练、prune） |

## CI 自动生成

`source/includes/tags/` 由前端 CI 从 Label Studio 标注标签 schema 生成，请勿手工编辑。
