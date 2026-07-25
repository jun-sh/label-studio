# UniFranka pick_place 时序相位边界规范（P0）

> **版本：** v1.0  
> **适用范围：** `unifranka` · `task_family=pick_place` · schema `pick_place_v1@1`  
> **对标：** Tesla Optimus / Figure 桌面抓取原语（非搬箱产线专用语义）  
> **配套：** [`pick_place_v1.annotation_schema.json`](./schemas/pick_place_v1.annotation_schema.json) · [`unifranka-annotation-plan.md`](./unifranka-annotation-plan.md)

---

## 1. 总则

1. **L0 任务文本只读**：`task_text` 描述「做什么」；L2 描述「怎么做」的原语段。  
2. **分段不重叠**；同相位连续动作合并。  
3. **一集多轮操作**（如「拿起再放回」）：每轮重复 `reach→…→release`，轮间用 `idle` 分隔；**不使用** L1.5 cycle 面板。  
4. **以腕摄/外摄可观测为准**（640×480 @ 5fps）。  
5. **L1 技能只读**：由 L2 自动推导，标注员不手改。

---

## 2. 八类相位边界

### `idle`
- **进入**：无明确操作目标；等待；上一轮 `release` 后手臂收回  
- **退出**：朝**当前操作目标**移动  

### `reach`
- **进入**：朝目标物体/区域移动，手未进入预抓  
- **退出**：手开始张开/伸向目标（→ `pre_grasp`）  
- **注意**：持物移动是 `transport`，不是 `reach`

### `pre_grasp`
- **进入**：对准目标，夹爪张开或调整姿态，**无接触**  
- **退出**：首次接触（→ `contact`）

### `contact`
- **进入**：首次碰到物体  
- **退出**：物体离开支撑面（→ `lift`）  
- **扔纸团**：抓稳纸团至出手前可标为 `contact`/`lift` 短段

### `lift`
- **进入**：物体离地  
- **退出**：开始朝目标位水平移动（→ `transport`）

### `transport`
- **进入**：持物移动中  
- **退出**：开始朝放置位下降（→ `place`）

### `place`
- **进入**：下降对位，尚未松手  
- **退出**：松手（→ `release`）

### `release`
- **进入**：夹爪张开，物体脱离  
- **退出**：手臂离开物体或下一轮 `reach` 开始

---

## 3. 典型任务判据

| task_text 类型 | 注意点 |
|----------------|--------|
| 扔纸团入垃圾桶 | `transport` 可能很短；`release` 在出手瞬间 |
| 叉子取放 | 标准八相；注意 `pre_grasp` 对准叉柄 |
| 取蛋 | `contact` 轻拿；`lift` 极低高度 |
| 杯盖 | 小物体；`pre_grasp` 可能仅数帧 |
| 多步「…then put back」 | 两轮完整八相，中间 `idle` |

---

## 4. 与搬箱规范 (431132) 差异

| 项 | 431132 box_transport | pick_place_v1 |
|----|----------------------|---------------|
| reach 语义 | 机身行走趋近箱体 | 手臂/机身朝**桌面物体** |
| transport 目标 | 传送带 | 任意放置目标 |
| 逐箱 cycle | 有 L1.5 | **无** |
| episode_fields | box_cycle | 仅 outcome |

---

## 5. 质检清单

- [ ] 同一 `task_text` 批量内 `reach`/`pre_grasp` 分界一致  
- [ ] 无 `pour`/`wipe`/`drag` 任务误用本规范  
- [ ] 多轮任务每轮有 `release` 或明确 `partial` outcome  
- [ ] outcome 与 L0 指令语义一致
