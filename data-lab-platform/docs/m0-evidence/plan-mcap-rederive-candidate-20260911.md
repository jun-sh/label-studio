# 最小落地方案：MCAP 完整派生候选（待确认）

**状态：** 方案提案，**未授权执行**。  
**目的：** 用本批冻结代码，从已有原始 MCAP 在隔离目录生成与「格式修复候选」并列的**完整派生候选**，供后续分项验收；不部署 130、不采集、不更新正式发布指针、不覆盖线上数据。

---

## 1. 与现有候选的区分

| | 格式修复候选（已有，冻结） | 完整派生候选（本方案） |
|--|---------------------------|------------------------|
| **ID 建议** | `ego-001-m1-20260911` | `ego-001-mcap-derived-20260911`（可调整） |
| **输入** | `corpus/ego_001` 复制 + timestamp 修补 | 四段原始 `stream/ego-001/raw/segments/*/seg_000001.mcap.zst` |
| **方法** | parquet 字段修补 | `deriveUnit` → publish 至隔离 corpus 布局（或等效 pipeline-unit 路径） |
| **证明范围** | LeRobot 格式可读性（loader 全帧） | 完整 derive 链 + 分项报告（见 §5） |
| **路径** | `data-storage/ego-delivery/candidates/ego-001-m1-20260911` | `data-storage/ego-delivery/candidates/ego-001-mcap-derived-20260911`（新建） |

---

## 2. 原始输入绑定

| Episode | Session | MCAP 路径（相对 `data-storage/`） |
|---------|---------|-----------------------------------|
| 0 | `sess_8aa298f87ba64b07b14afc7dbabab19a` | `stream/ego-001/raw/segments/.../seg_000001.mcap.zst` |
| 1 | `sess_de088591b4a94457b49c1a5278292f7d` | 同上 |
| 2 | `sess_2e8da6fee94a465e9a7fd79f8a6ab59e` | 同上 |
| 3 | `sess_48bcaf8d17c14fa08041d530b08ce734` | 同上 |

**执行时记录：** 每段 `sha256`、字节数、`frame_count`（materialize summary）；写入 `candidate_manifest.json` → `source_mcap_hashes`。

**设备时间字段（如实保留，不补造）：**
- ep0/ep1：MCAP observation **无** `primary_device_timestamp_ns`（0/699、0/325）
- ep2/ep3：**有**（202/202、380/380）；派生后 parquet 应含该列（依赖冻结版 `sync-stream-parquet.py`）

---

## 3. 代码版本绑定

执行前记录（写入 manifest，不修改 git）：

- `data-lab`：`git rev-parse HEAD` + 是否有未提交改动摘要
- `ego-platform`：同上（若 pipeline 涉及）
- LeRobot 官方：`e40b58a8` / 0.6.1，venv `/workspace/lerobot/.venv`（或本机等价路径）
- 关键脚本版本：`derive/unit.mjs`、`mcap-materialize.py`、`sync-stream-parquet.py` 与本批冻结一致

---

## 4. 执行步骤（隔离）

```
隔离根目录（示例）：
  data-storage/ego-delivery/work/mcap-rederive-20260911/
    station/          # 临时 stream station 根，非生产 ego-001
    corpus_out/       # 派生输出 → 复制为候选包
```

1. **准备隔离 station**  
   - 复制或 symlink 四段 `.mcap.zst` 至 `station/ego-001/raw/segments/<session>/`  
   - 复制 `stream/ego-001/meta/info.json` 作模板（仅元数据，不碰生产目录写权限）

2. **按 session 运行 derive**（复用现有入口）  
   - `deriveUnit('ego-001', stationRoot, sessionId)` × 4  
   - 合并/发布至 LeRobot v3 corpus 布局（复用 `pipeline-unit.mjs` / `ego-run-pipeline` 既有子命令，**不**新写状态机）

3. **生成候选包**  
   - 将合并结果复制到 `candidates/ego-001-mcap-derived-20260911/`  
   - 计算 `frozen_manifest`（文件清单 + sha256）  
   - **不**覆盖 `corpus/ego_001`、ORD、线上 ZIP

4. **分项验证**（仅新候选，不重复格式修复候选 1500 帧全量除非 manifest 变化）

---

## 5. 分项报告（输出 JSON，互相独立）

| 报告 | 脚本/入口 | 通过含义 |
|------|-----------|----------|
| **格式可读性** | `ego-lerobot-loader-gate.py`（**默认 head_mid_tail**；`--sample all` 仅发布回归可选） | 官方 loader 双后端可初始化并取样；timestamp 语义正确 |
| **视频对应** | `ego-h264-derive-output-verify.py` 逻辑扩展为四集或抽 ep2/ep3 全三路检查 | A1 帧数/PTS + A2 首中尾图像抽样；**非**全帧 |
| **IMU 状态** | `ego-imu-clock-audit.py` + `ego-imu-align-gate.py` | 字段存在性 + 时钟域审计；对齐仍为 **unverified** 除非有新增证据 |

汇总写入：`docs/m0-evidence/mcap-derived-candidate-20260911.json`（执行后生成）。

---

## 6. 资源与生产影响

| 项 | 估计 |
|----|------|
| **磁盘** | ~2–5 GB 临时 station + 候选副本（与四段 MCAP + MP4 体量相当） |
| **CPU/GPU** | derive remux 以 CPU/ffmpeg 为主；无 130 GPU 采集 |
| **耗时** | 约 4× 单 session derive（ep2 离线实测 ~1–2 min/集，合计约 **10–20 min** 量级，视磁盘而定） |
| **生产服务** | **不**重启线上 stream-ingest / 标注 / QC；**不**写 `data-storage/corpus/ego_001`、`data-storage/stream/ego-001` 热路径 |
| **网络** | 无（本地 MCAP） |

---

## 7. 发布仍缺少的验收条件（执行本方案后仍可能不足）

本方案**不**等同于可发布上线。发布前仍缺（或需运维/联调单独完成）：

1. **IMU 相机—时钟对齐**：`imu_align_verified` 仍为 unverified（除非重派生后补充新证据且门禁放行）
2. **上传封存**：隔离服务重放（D2）未在本方案内；生产 `DONE_UPLOAD` 未联调
3. **发布指针 / ORD**：manifest 更新、ZIP 置换、验收签收流程（D3）
4. **商业指标**：严格 30fps、6DoF、手部三维 — 本批与方案均不覆盖
5. **全库一致性**：四集 loader 全帧 + 双后端 — 仅对新候选执行，不与格式修复候选混为一谈

---

## 8. 确认后执行的前置条件

- [ ] 确认候选 ID 与隔离路径  
- [ ] 确认是否四集全部 derive 或先 ep2/ep3 试点  
- [ ] 确认 loader 全量 gate 是否对新候选必跑（建议：是，但仅一次）  
- [ ] 确认不触碰生产路径（脚本内硬编码 `WORK_ROOT`，禁止指向生产 corpus）

**请确认后回复「执行 MCAP 派生候选方案」；未确认前不运行。**
