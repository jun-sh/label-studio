# ego 上传 → 34 本机派生：交付测试计划与就绪评估

适用范围：ego-002（及后续同构工位）边缘采集 → `stream-ingest` 接收 → `derive-worker` 派生 → L2 LeRobot 发布 → 冷备。

## 1. 交付判定标准（全部满足才算"可交付"）

| 编号 | 标准 | 判定方式 |
|---|---|---|
| D1 | 不丢数据：边缘每个 CLOSED 段要么在 34 上 raw 落盘且有冷备 sha 标记，要么在 `delivery_quality.gaps` 中明确列出 | 对账脚本：seal ↔ raw ↔ cold 三方比对 |
| D2 | 不卡死：任何单点故障（断网、进程被杀、重启）后，无人工干预 30 分钟内恢复推进 | 故障注入 F1–F9 |
| D3 | 数据正确：每个 READY episode 帧数 = parquet 行数 = MP4 帧数（±1），L2 分片 `episode_index`/`index` 与 manifest 一致 | `validate_unit_manifest_data_shards` 0 错误 + LeRobot loader 全量读取 |
| D4 | 质量可追溯：带缺口交付的 session，READY 与 episodes 元数据中可见缺口段与原因 | 抽查 READY.delivery_quality |
| D5 | 吞吐达标：派生速度 ≥ 采集速度（单工位 8h/天采集，当天派生完） | 时间线 SLO 统计 |
| D6 | 可观测：每个 session 有完整时间线（segment_ingested → done_upload → derive_start → derive_ready/failed） | `live/session_timelines/*.jsonl` |

## 2. 单元 / 契约测试（CI 必跑）

已有并通过：

- seal 含 CORRUPT 段 → 跳过并记缺口；seal 外的游离 state 不阻塞；seal 内段失败仍阻塞（`session-coordinator.test.mjs`）
- 磁盘增量记账（`disk-accounting.test.mjs`）
- 发布锁：正常释放、异常释放、持有期拒绝、过期锁接管（`publish-lock.test.mjs`）
- 冷备：复制 + sha 校验 + 幂等（`raw-cold-backup.test.mjs`）
- 多 worker 并发 kick 只派生一次（`watch-kick.test.mjs`，已修复认领后二次检查）

待补：

| 编号 | 用例 |
|---|---|
| U1 | DERIVING 租约：过期回收、bootId 变化回收、进度心跳续租 |
| U2 | worker 重启回收同时清掉旧 `deriver.lock` |
| U3 | 增量发布：追加新 episode 只改一个分片；人为制造排序插队 → 自动回退全量重发，校验 0 错误 |
| U4 | 发布失败 → FAILED(DERIVE_TRANSIENT, category=publish) → 下一轮自动重试 |
| U5 | 冷备串行队列：并发入队 N 个文件，任一时刻只有 1 个 cp |

已知基线失败（与本次改动无关，需单独修）：`encode-pool.test.mjs`、`session-claim.test.mjs` 2 项。

## 3. 端到端场景（在 ego-002 真机 + 34 上执行）

| 编号 | 场景 | 期望 |
|---|---|---|
| E1 | 1 分钟短 session，正常停止 | 10 分钟内 READY，L2 新增 1 个 episode，编号 = 末尾 |
| E2 | 10 分钟 session | 同上；记录派生耗时/分钟素材 |
| E3 | 4 小时 / 239 段 session（`sess_2c91` 正在跑） | READY；记录总耗时、峰值内存；不阻塞上传 |
| E4 | 连续 5 个 session 积压后一次性上传 | 全部 READY，episode 编号连续无重复 |
| E5 | 带 CORRUPT 段的 session | READY，`delivery_quality.completeness < 1`，gaps 列出段号 |
| E6 | 同一 session 重复上传（重跑 ego-upload） | 去重，不产生新 episode，不改已发布编号 |
| E7 | 网页 collection 页面 | 新 episode 可播放、帧数/时长与时间线一致 |
| E8 | LeRobot 加载 | `LeRobotDataset` 全量迭代无异常，episode 数 = manifest |

## 4. 故障注入（鲁棒性核心）

| 编号 | 注入 | 期望恢复 |
|---|---|---|
| F1 | 上传中拔网线 / kill ego-upload | 恢复网络后续传，已 ACK 段不重传 |
| F2 | 上传中重启 `stream-ingest` | 边缘重试成功；半写文件不进入 raw |
| F3 | 派生中 `docker restart` derive-worker | 启动时回收 DERIVING + 锁，同一 session 重新派生（本轮已实测：01:37 中断 → 01:47 重新认领） |
| F4 | 发布中（python 写分片时）kill worker | 发布锁 10 分钟内被接管；分片无 `.tmp` 残留进入正式文件；校验 0 错误 |
| F5 | 34 磁盘写满（测试卷上模拟） | 上传返回明确错误，边缘保留本地数据不删除 |
| F6 | 冷备目录不可写 | 记录 `raw_cold_backup_fail`，不影响接收和派生 |
| F7 | 34 整机重启 | 所有容器自启，积压 session 自动继续 |
| F8 | 两个 worker 同时指向同一工位 | 同一 session 只派生一次 |
| F9 | 派生中删除一个 raw 段（模拟损坏） | session FAILED 且原因明确，不影响其他 session |

## 5. 数据质量验收

- 帧对齐：每 episode 帧数 / parquet 行数 / MP4 帧数
- 时间戳单调、无大于 2 帧的跳变（缺口段除外，且须在 gaps 中）
- 视频可在浏览器播放（`browser_playable=true`）；**READY 门禁应要求 mp4Ok 与 browser_playable 为真**（当前未强制）
- `meta/stats.json` 正常生成（当前有 "all input arrays must have the same shape" 告警，需定位）
- 冷备：raw 每段有 `.sha256.ok`，抽样 10% 重算一致

## 6. 性能 / SLO 基线（从时间线统计）

- 上传：≥ 50 MB/s（当前约 70 MB/s）
- done_upload → derive_ready：短 session P95 ≤ 15 分钟
- 增量发布：≤ 30 s（实测 12 s）；全量回退：记录耗时（约 6 分钟）
- 积压清空能力：每小时可派生的素材时长 ≥ 1 小时

## 7. 执行顺序

1. 补齐 U1–U5，CI 全绿（基线失败单独立项）
2. E1、E5、E6 → F3、F4（最常见故障）
3. E3（大 session）结束后统计 SLO
4. F1、F2、F5–F9
5. 连续 3 天真实采集观察，D1–D6 全部达标 → 签发可交付

### 7.1 ego-002 专用压测阶梯

002 在 **Tier 0 sign-off 通过前** 不要跑默认 `ego-stress-20min`（会清空 34 `stream/ego-002`）。逐步顺序与命令见 **[ego-002-edge-deploy.md §9](./ego-002-edge-deploy.md#9-ego-002-压测顺序对齐-ego-001最小阶梯)**（Tier 0 单段 E1 → Tier 1 `ego-stress-fast` → Tier 2 带 `EGO_STRESS_SKIP_CLEAN=1` 的短 soak）。
