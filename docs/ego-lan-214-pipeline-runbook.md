# ego-lan-214 全流程操作手册（极简版）

**设计原则：** 默认安全（增量、幂等）· 显式 `--reset` 才清场 · 单命令驱动 · 断点续跑

适用：**214** 采集 → **34** 上传 → **Viewer** 验收

---

## 99% 日常场景：3 步

| 步 | 在哪 | 做什么 |
|----|------|--------|
| 1 | 214 手机 | 连热点 → 网页 **开始 / 结束录制** |
| 2 | 214 终端 | `ego-export` → 拷 `ready/当日/` 下 `.tar.zst` |
| 3 | 34 | **214 Agent 上传**（`ecs-oak-upload-stack`）→ `ego-run-pipeline ego-lan-214` → Viewer |

**整轮 demo 重来**：第 2 步用 `ego-export --reset`，第 3 步用 `ego-run-pipeline ego-lan-214 --reset`。

---

## 两个核心命令

### 214：`ego-export`

```bash
ego-export          # 默认：只打包「已关段、未导出」的段，幂等可重复执行
ego-export --reset  # 整轮重来：清空 export/，重置段状态后重新导出
```

**自动处理：** 检测采集是否已停 · 跳过已导出段 · SHA256 校验 · 删 segments/ 源段 · 保留 ready/ 成品 · 输出摘要

**成品路径：** `/home/server/export/ego-lan-214/ready/YYYYMMDD/*.tar.zst`

### 34：`ego-run-pipeline`

```bash
ego-run-pipeline ego-lan-214          # 默认：增量处理未发布 session + 部署 Viewer
ego-run-pipeline ego-lan-214 --reset  # 整轮重来：全链路清理后从零跑
```

**自动处理（默认 oak 管线）：**

1. 发现待处理 session（无 `finalize.done` 的）
2. 轮询等待 mp4 + parquet 就绪
3. `ego_platform` oak convert（rectify → track → language → hands → corpus append）
4. 发布 zip 到 `data-storage/samples/`
5. `deploy-local-datasets.sh` 更新 Viewer

**回滚旧管线：**

```bash
EGO_PIPELINE_BACKEND=legacy ego-run-pipeline ego-lan-214
```

（legacy 以 `ego-hand-pipeline/outputs/.../.status/publish.done` 为完成标记。）

**验收地址：**

- 采集质检：<http://10.10.10.34:8080/collection?station=ego-lan-214>
- 处理后数据：<http://10.10.10.34:8080/data/ego_214_hand_pose>

---

## 一次性安装 CLI

**34 平台机：**

```bash
cd /path/to/data-lab
bash data-lab-platform/scripts/install-pipeline-cli.sh
# 确保 ~/.local/bin 在 PATH 中
```

**214 边缘机（随 ego-web 部署）：**

```bash
bash ~/ego-local-web-src/deploy.sh
# 安装 ego-export 到 ~/.local/bin
```

---

## 清理逻辑（无需人工判断）

| 场景 | 怎么做 | 会删数据吗 |
|------|--------|-----------|
| 日常追加采集 | `ego-export` + `ego-run-pipeline` | **否** |
| 后处理失败重跑 | 重新执行 `ego-run-pipeline` | **否**（断点续跑） |
| 只更新 Viewer | `ego-run-pipeline`（后处理已 done 则只 deploy） | **否** |
| 整轮 demo 重来 | 两边命令各加 `--reset` | **是**（须确认） |

**原则：** 无 `--reset` 永远不删历史数据；重置必须显式加参数并确认。

---

## 数据流（三层）

```text
原始数据 1   214 segments/          → ego-export
原始数据 2   34 stream/             → 214 Agent 上传（批量主通道）
处理数据 3   samples/*.zip          → ego-run-pipeline（oak）内部 publish + deploy
```

中间层 `data-storage/pipeline/<station>/<session>/` 由 oak 管线自动管理；legacy 回滚时仍写 `ego-hand-pipeline/outputs/`。

---

## 214 磁盘与 `ego-export` 删除行为

| 数据 | 导出成功后 |
|------|-----------|
| `segments/` 原始段 | **删除**（默认） |
| `export/ready/*.tar.zst` | **保留**（34 确认导入后手动清） |

**`EGO_SEGMENT_QUOTA_GB=256`**：`segments/` 待导出队列上限（**不是**总录制量）。按时 `ego-export` 即可循环使用；长期不导出则最老未导出段会被自动丢弃。

详见 [field-export-and-import.md §3.7](../data-lab-platform/ego-local-web/field-export-and-import.md)（配额与删除）及 [§3.8](../data-lab-platform/ego-local-web/field-export-and-import.md)（B/C 幂等判定）。

---

## 日志与排障

| 位置 | 内容 |
|------|------|
| 214 `.../ego-lan-214/logs/ego-export-*.log` | 导出摘要 |
| 34 `data-storage/logs/ego-run-pipeline-*.log` | 全链路日志 |
| `data-storage/pipeline/.../.status/*.json` | oak 各步骤状态 |
| `ego-hand-pipeline/outputs/.../postprocess.log` | legacy 回滚时后处理细节 |

| 现象 | 处理 |
|------|------|
| `ego-export` 报采集仍在运行 | 手机点「结束录制」 |
| `ego-export` 一段失败但 `ready/` 里已有该 tar | 见 [field-export-and-import.md §3.8](../data-lab-platform/ego-local-web/field-export-and-import.md)；更新 `export_offline.py` 后重跑 |
| `ego-export` 出现 `skipped stale` | 列表过期项，通常可忽略；见 §3.8 |
| `ego-run-pipeline` 报没有数据 | 先用 214 Agent 上传；单段可浏览器「导入」补传 |
| 后处理报缺 mp4 | 等几分钟再重跑同一条命令 |
| 任意步骤失败 | **原样重跑同一条命令**（幂等续跑） |

---

## 高级 / 兼容：原有分步脚本仍可用

| 用途 | 脚本 |
|------|------|
| 全链路清场 | `data-lab-platform/scripts/ego-lan-214-reset-for-rerun.sh` |
| 等待 mux 就绪 | `ego-lan-214-wait-ready-for-postprocess.sh` |
| 单 session oak convert | `ego-oak-convert --station ego-lan-214 --session sess_...` |
| 单 session legacy 后处理 | `ego-hand-pipeline/scripts/ego-postprocess.sh` |
| 仅部署 Viewer | `data-lab-platform/deploy-local-datasets.sh` |
| 214 底层导出 | `export-offline.sh` / `export_offline.py` |

技术细节：[field-export-and-import.md](../data-lab-platform/ego-local-web/field-export-and-import.md) · [ego-lan-214-segment-storage-and-upload.md](./ego-lan-214-segment-storage-and-upload.md) · [ego-dataset-management.md](./ego-dataset-management.md) · [ego-platform-release-2026-07-08.md](./ego-platform-release-2026-07-08.md)

---

## 完整命令复制（整轮 demo）

```bash
# === 34：清场（仅整轮重来时需要）===
ego-run-pipeline ego-lan-214 --reset

# === 214：采集后 ===
ego-export --reset   # 或默认 ego-export

# === 34：214 Agent 上传后 ===
ego-run-pipeline ego-lan-214
```
