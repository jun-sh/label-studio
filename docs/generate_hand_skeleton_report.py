#!/usr/bin/env python3
"""Generate hand skeleton technical report (Word)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


def set_doc_font(doc: Document, font_name: str = "宋体", font_name_ascii: str = "Times New Roman") -> None:
    style = doc.styles["Normal"]
    style.font.name = font_name_ascii
    style.font.size = Pt(11)
    style._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)


def add_title(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(18)
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")


def add_heading(doc: Document, text: str, level: int = 1) -> None:
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.name = "Times New Roman"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
        run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)


def add_para(doc: Document, text: str, *, bold: bool = False, indent: bool = True) -> None:
    p = doc.add_paragraph()
    if indent:
        p.paragraph_format.first_line_indent = Cm(0.74)
    p.paragraph_format.line_spacing = 1.5
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(11)
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def add_bullet(doc: Document, text: str) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.line_spacing = 1.5
    run = p.add_run(text)
    run.font.size = Pt(11)
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def build_report() -> Document:
    doc = Document()
    set_doc_font(doc)

    today = date.today().strftime("%Y年%m月")
    add_title(doc, "第一人称手部骨架估计技术路线研究报告")
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run(f"（内部技术文档 · {today} · 密级：内部公开）")
    r.font.size = Pt(10)
    r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    doc.add_paragraph()

    add_heading(doc, "摘要", 1)
    add_para(
        doc,
        "本报告面向 OAK-4P 四路鱼眼第一人称采集场景，系统梳理手部骨架（Hand Skeleton）"
        "从原始视频到可交付训练数据与可视化 overlay 的完整技术路线。路线以单目参数化手部模型"
        "回归为核心范式，在开源预训练骨干之上，通过几何校正、检测先验、时序约束与分层质量"
        "门控等工程—算法协同手段，提升 ego 视角下的可用精度与稳定性。报告在保持一定抽象"
        "度的前提下，阐明各阶段的信息论角色、误差来源与原创性改进方向，并给出主要依赖"
        "的开源组件清单，供研发、交付与专利布局参考。",
    )

    add_heading(doc, "1. 问题定义与系统边界", 1)
    add_heading(doc, "1.1 任务表述", 2)
    add_para(
        doc,
        "在固定头戴式多相机 rig 下，对每一帧图像估计双手的二维关键点与三维关节状态，"
        "并统一映射至 LeRobot v3 观测空间（observation.hands，126 维）及 Viewer 侧车"
        "（hand_kp2d overlay）。该任务可形式化为：在给定内参已知的透视投影模型下，"
        "寻找使重投影残差与时序一致性联合最小的手部姿态序列。",
    )
    add_heading(doc, "1.2 约束与难点", 2)
    add_bullets = [
        "强透视与自遮挡：手腕靠近镜头时，关节深度变化剧烈，单目深度歧义显著；",
        "鱼眼畸变：原始成像偏离针孔模型，须在统一 rectified 坐标系下推理与评估；",
        "左右手不对称误差：ego 场景下手部尺度、检测置信与 MANO chirality 失配呈现系统性偏差；",
        "多 session 聚合：需在语料库级别保持任务命名、episode 元数据与 overlay 契约一致。",
    ]
    for b in add_bullets:
        add_bullet(doc, b)

    add_heading(doc, "2. 总体技术架构", 1)
    add_para(
        doc,
        "系统采用分层流水线（Layered Perception Stack）设计：底层负责传感器几何与运动"
        "状态估计，中层负责单帧手部感知与参数回归，上层负责时序融合、质量筛选与"
        "训练—可视化双轨导出。生产路径由 ego-platform 编排，离线增强路径由 ego-hand-pipeline"
        "承载，二者在 MANO 参数语义与 kp2d 契约上保持对齐。",
    )
    add_para(doc, "概念性数据流如下（自底向上）：", bold=True)
    flow_items = [
        "原始段（fisheye MP4 + EEPROM 内参）",
        "→ 几何校正（Rectify / Undistort）",
        "→ 手部检测与裁剪（Detection & Crop）",
        "→ 参数化回归（MANO via HaMeR）",
        "→ 重投影质检（Reprojection QC）",
        "→ 时序后处理（Smoothing & Gap Repair）",
        "→ 多视择优融合（Stereo Selection，非联合优化）",
        "→ LeRobot 写入 + Overlay 发布 + 发布门禁",
    ]
    for item in flow_items:
        add_bullet(doc, item)

    add_heading(doc, "3. 分阶段技术路线", 1)

    add_heading(doc, "3.1 几何与运动基座", 2)
    add_para(
        doc,
        "手部精度上界受相机模型约束。系统首先将四路鱼眼帧映射至 pinhole rectified 空间，"
        "并持久化 new_camera_matrix 供后续重投影与 overlay 使用。并行地，IMU 积分提供"
        "ego 运动先验（observation.pose），为后续多模态对齐预留接口，但当前手部主干"
        "不直接依赖 VIO 输出。",
    )
    add_para(doc, "主要开源依赖：", bold=True)
    add_bullet(doc, "OpenCV（cv2.fisheye）：鱼眼去畸变与视频编解码；")
    add_bullet(doc, "FFmpeg：rectified H.264 转码与 Viewer 降采样发布。")

    add_heading(doc, "3.2 手部检测与感兴趣区域构建", 2)
    add_para(
        doc,
        "检测阶段在 rectified 帧上产生左右手候选框。默认采用 MediaPipe Hand Landmarker"
        "（Tasks API），可选 Ultralytics YOLO11 作为后备；在检测失效时退化为 ego 场景"
        "启发式中心裁剪。该阶段输出并非最终骨架，而是为回归网络提供尺度归一化的输入域。",
    )
    add_para(doc, "主要开源依赖：", bold=True)
    add_bullet(doc, "Google MediaPipe（Hand Landmarker / legacy solutions.hands）；")
    add_bullet(doc, "Ultralytics YOLO11（yolo11n.pt，手部类别过滤）。")
    add_para(doc, "原创性增强（相对开箱即用）：", bold=True)
    add_bullet(doc, "Ego 启发式框评分：结合位置、面积与长宽比，偏好画面中下区域，抑制背景误检；")
    add_bullet(doc, "双手分槽策略 detect_hands_both：左右手独立择优，避免单框竞争；")
    add_bullet(doc, "camera_front_right 镜像校正：针对右前视相机 MediaPipe handedness 系统性颠倒（QA 显示 67%–92% 高 reproj 帧可归因于标签镜像），按相机差异化启用/禁用，避免左相机误校正。")

    add_heading(doc, "3.3 参数化手部回归（核心）", 2)
    add_para(
        doc,
        "在 224×224 裁剪域内，采用 HaMeR（Hand Mesh Recovery）回归 MANO 姿态参数"
        "（63D pose + 10D shape β + 3D translation），并输出 21 关节 2D/3D 关键点。"
        "该步骤将高维图像观测压缩至低维流形，是整条链路的精度瓶颈所在。",
    )
    add_para(doc, "主要开源依赖：", bold=True)
    add_bullet(doc, "HaMeR（geopavlakos/hamer，ViT 骨干 + MANO 解码）；")
    add_bullet(doc, "MANO / smplx（MANO_RIGHT.pkl 参数化手部模型）；")
    add_bullet(doc, "PyTorch、timm、einops（推理运行时）。")
    add_para(doc, "原创性增强（相对开箱即用）：", bold=True)
    add_bullet(doc, "Rectified 坐标系端到端契约：crop→推理→UV 反投影→重投影 QC 全程在 pinhole K 下闭环，而非在原始鱼眼域直接套用预训练模型；")
    add_bullet(doc, "高 reproj 回退机制：时序平滑后若重投影误差超过阈值 1.5×，回退未平滑 pose/trans，抑制过度平滑导致的漂移；")
    add_bullet(doc, "双轨导出：MANO 训练轨（mano_kp2d_uv）与 MediaPipe 预览轨（mediapipe_kp2d_uv）并行，解耦训练精度与 Viewer 观感。")

    add_heading(doc, "3.4 时序一致性与帧修复", 2)
    add_para(
        doc,
        "单帧回归在快速运动中存在高频抖动。系统在序列维度引入 One-Euro 滤波器"
        "（姿态与平移）及 shape 参数的指数滑动平均，并对短时缺失帧进行线性插值修复。"
        "frame_quality 标志（0=正常，1=插值，2=劣质）为下游 export 与 overlay 提供"
        "可解释的质量语义。",
    )
    add_para(doc, "原创性模块（自研，非第三方库直接提供）：", bold=True)
    add_bullet(doc, "mano_smooth.py / frame_repair.py：MANO 参数 One-Euro + EMA + 短 gap 修复；")
    add_bullet(doc, "mediapipe_smooth.py：预览轨 2D UV 独立平滑与修复管线。")

    add_heading(doc, "3.5 质量门控与分层验收", 2)
    add_para(
        doc,
        "ego 场景下左右手误差分布显著非对称。系统放弃单一全局阈值，采用左右手"
        "分层重投影阈值与最小手部面积约束，并引入 hand_confidence 软标记："
        "左手低质量帧保留 UV 与降置信显示，而非 export 层整行清零——该策略直接"
        "解决了早期链路中「检测存在但 Viewer 左手全灭」的断层问题。",
    )
    add_para(doc, "典型阈值设计（可随站点配置）：", bold=True)
    add_bullet(doc, "右手：reproj ≤ 12 px，min_hand_area ≥ 0.40；")
    add_bullet(doc, "左手：reproj ≤ 96 px，min_hand_area ≥ 0.22（放宽以保留可用弱监督信号）；")
    add_bullet(doc, "B3 验收：双手有效帧 union 比例 ≥ 85%（publish_gate + quality.py）。")

    add_heading(doc, "3.6 多视融合策略（当前代际）", 2)
    add_para(
        doc,
        "双前视相机各自独立运行单目 HaMeR。当主相机某手缺失时，从副相机按最低"
        "重投影误差择优填充 observation.hands（_fuse_stereo_observation_hands）。"
        "需明确指出：该策略属于质量驱动的观测选择（Observation Selection），"
        "而非双目三角化或联合重投影 Bundle Adjustment。多视几何联合优化列为下一代"
        "（P2）研究方向。",
    )

    add_heading(doc, "3.7 交付与可视化", 2)
    add_para(
        doc,
        "后处理产物经 corpus append 聚合为多 episode LeRobot v3 语料，zip 发布至"
        "Viewer，hand_kp2d sidecar 驱动前端骨架 overlay。发布门禁（publish_gate）"
        "硬约束内参有效、rectify 未跳过、任务命名非 unknown、四路视频与深度预览"
        "完整——将感知质量前移至交付边界。",
    )
    add_para(doc, "Viewer 侧原创渲染策略：", bold=True)
    add_bullet(doc, "左右手分层 reproj 阈值读取 payload 元数据；")
    add_bullet(doc, "低置信左手降透明度软渲染，避免硬隐藏造成的「骨架消失」观感。")

    add_heading(doc, "4. 开源组件总表", 1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    hdr[0].text = "组件"
    hdr[1].text = "用途"
    hdr[2].text = "引用 / 版本"
    rows = [
        ("HaMeR", "MANO 参数回归、2D/3D 关键点", "geopavlakos/hamer (PyTorch)"),
        ("MANO / smplx", "手部参数化模型", "smplx==0.1.28, MANO_RIGHT.pkl"),
        ("MediaPipe", "手部检测与 21 点 2D", "Hand Landmarker Tasks API"),
        ("Ultralytics YOLO11", "可选手部检测后备", "yolo11n.pt"),
        ("OpenCV", "鱼眼校正、投影、可视化", "cv2.fisheye"),
        ("PyArrow / NumPy", "Parquet sidecar、数值计算", "LeRobot v3 I/O"),
        ("Depth Anything V2", "相对深度预览（非手部主干）", "HuggingFace transformers"),
        ("FFmpeg", "视频转码与 Viewer 降采样", "系统依赖"),
    ]
    for comp, use, ref in rows:
        row = table.add_row().cells
        row[0].text = comp
        row[1].text = use
        row[2].text = ref
    doc.add_paragraph()

    add_heading(doc, "5. 原创性工作归纳", 1)
    add_para(
        doc,
        "本系统在开源预训练模型之上，并非简单「调用 API」，而是在 ego 鱼眼—多相机—"
        "双手不对称误差这一特定物理场景下，构建了从几何、检测到 export、再到可视化"
        "的闭环改进体系。可概括为以下四类原创贡献：",
    )
    orig = [
        (
            "几何—感知闭环",
            "将 HaMeR 推理约束在 rectified pinhole 空间，并以该空间下的 K 矩阵做重投影 QC，"
            "使预训练模型的误差评估与真实部署坐标系一致。",
        ),
        (
            "左右手非对称质检",
            "基于大规模 QA 统计发现左手高 reproj 主因是 MANO chirality 失配而非单纯检测缺失，"
            "据此设计分层阈值、hand_confidence 软导出与右相机 handedness 镜像校正。",
        ),
        (
            "时序鲁棒后处理",
            "One-Euro + 短 gap 修复 + 高 reproj 回退的三段式时序策略，在保持运动细节的同时"
            "抑制 ego 快速操作导致的骨架闪烁。",
        ),
        (
            "交付级质量门禁",
            "将内参、rectify、任务命名、双手有效率纳入 publish_gate，使「不可用数据」"
            "无法进入 Viewer 与训练语料，从系统层面保障精度可验收。",
        ),
    ]
    for i, (title, desc) in enumerate(orig, 1):
        add_para(doc, f"（{i}）{title}：{desc}")

    add_heading(doc, "6. 误差分析与演进路线", 1)
    add_heading(doc, "6.1 主要误差来源（理论视角）", 2)
    err = [
        "投影歧义：单目深度不可观，MANO 在快速深度变化时易出现 chirality 翻转；",
        "检测—回归域偏移：裁剪框偏小或 handedness 错误导致回归输入分布偏离训练域；",
        "时序断裂：遮挡与 motion blur 造成短时 track 丢失，需插值与质量标记；",
        "多视不一致：独立单目推理缺乏跨相机几何约束，双路骨架可能不同步。",
    ]
    for e in err:
        add_bullet(doc, e)

    add_heading(doc, "6.2 演进路线（P0 → P2）", 2)
    roadmap = [
        ("P0（已落地）", "右相机镜像校正、左右分层 QC、hand_confidence 软导出、overlay 分层渲染"),
        ("P1（进行中）", "2D kp2d 时序平滑增强、track_id 关联、双相机偏差 QA 报表自动化"),
        ("P2（规划）", "双目三角化初值、多视联合重投影优化、bimanual MANO 全局一致性约束、在线多目跟踪"),
    ]
    for phase, content in roadmap:
        add_para(doc, f"{phase}：{content}")

    add_heading(doc, "7. 结论", 1)
    add_para(
        doc,
        "本团队手部骨架技术路线以 HaMeR + MANO 开源骨干为算法内核，面向 OAK-4P ego"
        "采集场景构建了完整的几何校正、检测先验、时序后处理、分层质检与交付门禁体系。"
        "原创性工作集中于将通用预训练模型适配至鱼眼第一人称域，并通过数据驱动的"
        "左右手非对称策略显著提升可用精度与 Viewer 观感。下一代工作将沿多视几何联合"
        "优化方向演进，在保持当前交付稳定性的前提下逐步提高三维一致性。",
    )

    add_heading(doc, "参考文献与开源链接", 1)
    refs = [
        "Pavlakos et al., Reconstructing Hands in 3D with Transformers (HaMeR), CVPR 2024.",
        "Romero et al., Embodied Hands: Modeling and Capturing Hands and Bodies Together (MANO), SIGGRAPH Asia 2017.",
        "https://github.com/geopavlakos/hamer",
        "https://github.com/vchoutas/smplx",
        "https://developers.google.com/mediapipe",
        "https://github.com/ultralytics/ultralytics",
        "https://github.com/DepthAnything/Depth-Anything-V2",
        "内部：ego-platform / ego-hand-pipeline / hand-overlay-p0p1-rules.md",
    ]
    for i, ref in enumerate(refs, 1):
        p = doc.add_paragraph(style="List Number")
        p.paragraph_format.line_spacing = 1.5
        run = p.add_run(ref)
        run.font.size = Pt(10)
        run.font.name = "Times New Roman"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    doc.add_paragraph()
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = footer.add_run("— 文档结束 —")
    fr.font.size = Pt(9)
    fr.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    return doc


def main() -> None:
    out = Path(__file__).resolve().parent / "ego-hand-skeleton-technical-report.docx"
    doc = build_report()
    doc.save(out)
    print(out)


if __name__ == "__main__":
    main()
