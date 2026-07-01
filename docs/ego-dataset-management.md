# Data Lab — EGO 数据集管理（平台侧）

平台 Viewer（`/data/<dataset_id>`）用于**浏览与质检**，不具备正式训练数据的删除权限。训练交付走后处理离线管线。

详细规范见 ego-hand-pipeline 文档：

- [数据集管理总原则](../../ego-hand-pipeline/docs/dataset-management-overview.md)
- [Task 命名规则](../../ego-hand-pipeline/docs/task-naming.md)
- [Prune 与 Publish](../../ego-hand-pipeline/docs/prune-and-publish.md)

## Viewer 里「编辑 → 删除 Episode」是什么

LeRobot Studio 上游实现的**浏览器会话级软删除**：

- 标记保存在当前页面内存（`deletedEpisodes` Set）
- **不修改** `/srv/bundled/*.zip` 或 stream aggregate
- 删除后 episode 编号**不重排**（删 #0、#1 后剩 `#2` 仍显示为 `#2`）
- 可「恢复」；导出子集时可排除已删项
- 刷新页面后删除状态丢失

**禁止**将 Viewer 删除当作训练数据集的最终裁剪方式。

## 正式训练数据从哪来

| 步骤 | 工具 | 产出 |
|------|------|------|
| 采集 | `ego-stream-client` / stream-ingest | `data-storage/stream/<station>/` |
| 后处理 | `ego-hand-pipeline/scripts/ego-postprocess.sh` | session dataset → append 到 aggregate |
| 可选裁剪发布 | `publish --prune-remove 0,1` | `*-release-*` 目录 + `samples/<slug>.zip` |
| 平台展示 | `deploy-local-datasets.sh` | Viewer `/data/ego_214_hand_pose` |

## 发布到本机 Viewer 示例

在 ego-hand-pipeline 完成 postprocess 后：

```bash
# publish 已写入 data-lab/data-storage/samples/ego_214_hand_pose.zip
cd data-lab
bash data-lab-platform/deploy-local-datasets.sh
```

访问：`http://<host>:8080/data/ego_214_hand_pose`

## hand_kp2d 叠加层

Viewer 手部关键点叠加读取：

```
/lerobot/api/sample/<dataset_id>/hand-kp2d.json
```

对应文件：`data-storage/samples/<slug>_hand_kp2d.json`，由 `step_publish` 从 release/aggregate 的 `offline/` 导出。Prune 发布后会按新 episode 索引重建。

## 相关配置

- 样例列表：`data-lab-platform/lerobot-studio/config/datasets.json`
- 样例 manifest：`config/sample-datasets.manifest.json`
- 采集站：`config/collection-stations.json`
