# ego-lan-214 Agent 上传操作员手册（商用交付版）

**适用**：214 边缘站 `ecs-oak-upload-stack` · 站点 `ego-lan-214`  
**通道策略**：[ego-lan-214-upload-channel-policy.md](./ego-lan-214-upload-channel-policy.md)

---

## 一、操作员只看 4 件事

| # | 关心点 | 怎么判断 |
|---|--------|----------|
| 1 | **进度** | `ego-upload-status` → `进度：N/M 段` |
| 2 | **状态** | 传输中 / 空闲 / 跳过 / 失败 |
| 3 | **断了能续** | 再 `start` 上传服务，自动接着传 |
| 4 | **重复不浪费** | 日志「已存在，自动跳过」；P1 后几乎不占带宽 |

---

## 二、标准操作流程

### 2.1 开始上传

**一句命令（上传 + 终端内实时进度，无需另开窗口）：**

```bash
ego-upload
```

终端会持续刷新同一块面板，例如：

```text
ego-upload · sess_8c8c…6c4aa0ae
[█████░░░░░░░░░░░░░░░░░] 24% · 5/21 段 · 剩余 ~12 分钟
▶ seg_000018 · 78 MB · 45s · 校验 SHA256
```

完成后：

```text
ego-upload · sess_8c8c…6c4aa0ae
[██████████████████████] 100% · 21/21 段 · 速度：5.1 MB/s
→ 数据已送达服务器，后台派生中请耐心等待。
✅ 上传完成（21 段），查询派生进度：ego-derive-status
```

仅查看进度（未在上传时）：`ego-upload --status`

**首次若提示 `command not found`**（214 上执行一次，需 sudo 密码，与 `ego-export` 相同）：

```bash
sudo bash /home/server/ego-web/install-ego-cli.sh
# 或仓库路径：sudo bash …/ego-local-web/scripts/install-ego-cli.sh
```

安装后可用：`ego-export`、`ego-upload`、`ego-derive-status`（查 34 派生进度）。

之后新开终端直接 `ego-upload`，**无需** `source ~/.bashrc`。

```bash
ego-upload --source ready   # 强制从 export/ready/ 传
```

**后台服务（扫 segments/ 待传段，适合录完持续传）：**

```bash
systemctl --user start ecs-oak-upload-stack.target
ego-upload --status   # 查看 loop 进度
```

### 2.2 查看进度

上传过程中 **`ego-upload` 已自带实时面板**；传输结束后面板停在最终状态。

未运行上传时，可单独查询：

```bash
ego-upload --status
```

### 2.3 判断传完

- **214 侧（上传）**：`ego-upload --status` 显示 `进度：21/21`、`状态：空闲`；上传结束后提示 **「34 后台已启动派生」** 及 `ego-derive-status`
- **34 侧（派生）**：**上传完成 ≠ 可回放**；三级状态见下表

| 状态 | 含义 | 操作员动作 |
|------|------|------------|
| **已上传 UPLOADED** | Raw 已入库，派生未开始 | 等待 `ego-upload` 自动 kick，或 60s 兜底 |
| **派生中 DERIVING** | 后台 jsonl/parquet/MP4 | `ego-derive-status --watch` |
| **已就绪 READY** | 磁盘校验通过 | 打开采集页回放 |

**P0 隔离（默认开启）**：

- `DERIVE_STANDALONE=1` — 上传 HTTP 与派生批处理**彻底隔离**（商用架构）；`stream-ingest` 只写 `session.DONE_UPLOAD`，派生由 `derive-worker` / `ego-derive run` 执行
- `DERIVE_DEFER_UNTIL_UPLOAD=1` — 21 段传完后 `ego-upload` 自动 `POST …/derive-start`（standalone 模式下写标记，不阻塞 HTTP）

手动补启派生：`ego-derive run` 或 `ego-rederive`；`curl -X POST …/derive-start` 写 `session.DONE_UPLOAD` 后由 worker 接管。

#### 2.3.1 派生进度（与上传对称）

```bash
# 从 raw/ 一键重派生（对称 ego-upload，默认终端实时进度）
ego-rederive
ego-rederive --start-only        # 已 prepare 过，只启动队列
ego-rederive --no-watch          # 启动后只打一次状态

# 仅查进度
ego-derive-status

# 214 查 34
EGO_DATALAB_BASE_URL=http://10.10.10.34:8080 ego-derive-status
```

显示：**已派生 N/M**、当前派生段、并发数、ETA、失败段及原因、日志排查命令。

采集页工具栏也会显示 **已派生 N/M** 及状态徽章（已上传 / 派生中 / 已就绪 / 失败）。

#### 2.3.2 如何确认「派生成功」

**派生** = 34 后台把已签收的 `tar.zst`（Raw）解压 → 写 jsonl → 合成四路 MP4 → 写 LeRobot **parquet**（可回放格式）。  
与 214 上的 **`ego-export`（打 tar.zst 包）** 不是同一步；214 导出 → `ego-upload` 传 Raw → **34 自动派生**，无需在 214 再按「解压」按钮。

| 方式 | 命令 / 操作 | 通过标准 |
|------|-------------|----------|
| **派生进度 CLI（推荐）** | `ego-derive-status` 或 `--watch` | `已派生 21/21`、状态「已就绪」 |
| **验收脚本** | 在 **34** 上：`CHECK_DERIVE_ASYNC=1 bash data-lab-platform/scripts/ego-lan-214-reimport-verify.sh` | 全绿，`derived count=21` |
| **段状态 API（8080）** | `curl -s 'http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/derive-status' \| jq .progress` | `ready >= 21` |
| **段列表 API** | `curl -s 'http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/segments?status=derived' \| jq '.segments \| length'` | `>= 21` |
| **采集页回放** | 打开 [采集页](http://10.10.10.34:8080/collection?station=ego-lan-214) | 工具栏「已派生 N/N · 已就绪」；时间轴帧数 = 段数×300 @30Hz |
| **处理中** | `ego-derive-status` 或采集页徽章「派生中」 | 等几分钟后再查 |

派生通常 **数分钟～十几分钟**（21 段 × 每段解压+mux）。W2 商用出口以 **ego-derive-status 全绿 + 验收脚本 + 采集页回放** 为准。

> 采集页已提供会话级 **已派生 N/M**；段级五态总览见商用 PRD 后续迭代。

### 2.4 停止上传

```bash
systemctl --user stop ecs-oak-upload-stack.target
```

未标 `uploaded` 的段下次 `start` 会自动继续。

---

## 三、状态文件

| 项 | 值 |
|----|-----|
| 默认路径 | `$XDG_RUNTIME_DIR/ego-upload-status.json` |
| 覆盖 | 环境变量 `EGO_UPLOAD_STATUS_PATH` |
| 写入方 | `ecs-upload-segments-loop` + 单段上传子进程 |

---

## 四、日志人话对照表

| 旧日志 | 含义 | 操作 |
|--------|------|------|
| `pending=11` | 还有 11 段待传 | 正常排队 |
| `pending=0 idle` | 本机队列已空 | 可 stop；34 查派生 |
| `segment_ok … duplicate=False` | 该段首次送达 34 | 正常 |
| `segment_ok … duplicate=True` | 34 已有该段，跳过入库 | 正常，不重复存储 |
| `segment_skip … missing_manifest` | 段目录残缺 | **非上传故障**；联系运维 |
| `segment_fail` | 该段多次重试仍失败 | 查网络/34 服务；段仍留在队列 |
| `Broken pipe`（传输中） | 连接被掐断（终端关闭、管道截断） | 再跑 `ego-upload`；成功后异常行自动隐藏 |
| `frames=0` | 异步模式只签收 Raw | **不是失败** |

---

## 五、客户 FAQ（可直接复制）

### 1. 上传中断后还能继续吗？

可以。重新执行 `systemctl --user start ecs-oak-upload-stack.target` 后，系统会从**未完成的段**自动继续，无需重新选择文件或清空任务。

### 2. 重复上传会浪费流量吗？

不会重复入库。平台按「会话 + 段编号」识别，已完成的段会**自动跳过**。优化后（34 提前判重）重复上传几乎不消耗带宽。

### 3. 为什么有的片段显示「跳过」？

部分段因数据不完整（例如缺失清单文件）被**自动隔离**，不影响其他段上传。需运维检查采集收尾是否正常。

### 4. 上传完成为什么不能立刻回放？

上传代表数据已送达平台；后台需异步生成可播放文件（`DERIVE_ASYNC=1`）。处理完成后即可在 34 采集页回放。

### 5. 怎么判断全部完成？

| 阶段 | 条件 |
|------|------|
| 上传完成 | 214 `ego-upload --status` 待传 = 0 |
| 派生完成 | `ego-derive-status` 显示 **已就绪**；或采集页 **已派生 N/N** |
| 可回放 | 34 验收脚本全绿 / 段状态全部 `derived` |

---

## 六、异常处理

| 现象 | 处理 |
|------|------|
| `未找到采集会话` | 先确认已录制并关段；检查 `segments/sessions/` |
| 长时间卡在同一段 | 查 34 网络、`curl` upload URL；看 `segment_fail` |
| 多段 `missing_manifest` | 采集异常或手工删了 manifest；勿反复 start 指望自愈 |
| 214 显示完成但 34 无数据 | `ego-derive-status`；查 `DERIVE_ASYNC`、stream-ingest 日志 |
| 派生失败 / 锁超时 | `ego-derive-status` 看失败段；`docker logs data-lab-stream-ingest-1 … \| grep derive_fail` |
| MP4 帧数不齐 / 长期 MUXING | 34 上按 [独立派生运维](./ego-lan-214-standalone-derive-operator.md#线上异常mp4-帧数不对--长期卡在mp4-合成中) 执行全量 remux；**无需重传 214** |

---

## 七、相关文件（仓库）

| 组件 | 路径 |
|------|------|
| 状态模块 | `data-lab-platform/ego-stream-client/upload_status.py` |
| 派生状态 CLI | `data-lab-platform/ego-stream-client/derive_status.py`、`scripts/ego-derive-status` |
| 上传循环 | `data-lab-platform/ego-stream-client/tools/upload_segments_loop.py` |
| CLI | `data-lab-platform/ego-stream-client/cli/upload_status.py` |
| 包装脚本 | `data-lab-platform/scripts/ego-upload`（214 部署：`/home/server/ego-web/ego-upload`） |
| systemd | `ego-stream-client/systemd/ecs-upload-segments-loop.service` |
| PRD | [ego-lan-214-agent-upload-commercial-prd.md](./ego-lan-214-agent-upload-commercial-prd.md) |

---

## 八、214 部署同步

将以下文件同步到 214 `ego-studio` 对应路径后重启上传服务：

- `capture/upload_status.py`
- `capture/segment_upload.py`
- `tools/upload_segments_loop.py`
- `cli/upload_status.py`

```bash
systemctl --user restart ecs-upload-segments-loop.service
```

34 侧若启用 P1 提前判重，需重启 `stream-ingest`。
