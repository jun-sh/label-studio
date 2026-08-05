/**
 * SenseXperience-style rich hand overlay (pure functions, no DOM).
 * Draw order: mask → bbox → bones → joints → ID label.
 */

/** MediaPipe MANO topology (21 joints). */
export const HAND_EDGES = [
  [0, 1], [1, 2], [2, 3], [3, 4],
  [0, 5], [5, 6], [6, 7], [7, 8],
  [0, 9], [9, 10], [10, 11], [11, 12],
  [0, 13], [13, 14], [14, 15], [15, 16],
  [0, 17], [17, 18], [18, 19], [19, 20],
];

/** Per-joint RGB aligned with SenseXperience camera_02 (thumb white … pinky black). */
export const FINGER_JOINT_COLORS = [
  [255, 255, 255], // 0 wrist
  [255, 255, 255], [255, 255, 255], [255, 255, 255], [255, 255, 255], // thumb
  [255, 0, 0], [255, 0, 0], [255, 0, 0], [255, 0, 0], // index
  [0, 200, 0], [0, 200, 0], [0, 200, 0], [0, 200, 0], // middle
  [0, 80, 255], [0, 80, 255], [0, 80, 255], [0, 80, 255], // ring
  [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], // pinky
];

export const BONE_COLOR = [255, 255, 255];

export const HAND_MASK_COLORS = {
  left: [246, 130, 59, 0.35],
  right: [59, 130, 246, 0.35],
};

export const HAND_ID_LABELS = {
  left: "ID:1 L",
  right: "ID:2 R",
};

export const RICH_RENDER = {
  BONE_LINE_WIDTH: 2.5,
  JOINT_RADIUS: 3.5,
  BBOX_LINE_WIDTH: 2,
  LABEL_FONT_SIZE: 11,
  BBOX_PADDING_RATIO: 0.03,
  MIN_BBOX_PADDING_PX: 4,
};

function cross(o, a, b) {
  return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

/** Monotone-chain convex hull (Graham scan variant). */
export function convexHull(points) {
  const pts = (points || [])
    .filter((p) => p && Number.isFinite(p.x) && Number.isFinite(p.y))
    .map((p) => ({ x: p.x, y: p.y }));
  if (pts.length <= 1) return pts.slice();
  pts.sort((a, b) => (a.x === b.x ? a.y - b.y : a.x - b.x));

  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }
  const upper = [];
  for (let i = pts.length - 1; i >= 0; i -= 1) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

export function bboxFromPoints(points) {
  const pts = (points || []).filter((p) => p && Number.isFinite(p.x) && Number.isFinite(p.y));
  if (!pts.length) return { x: 0, y: 0, width: 0, height: 0 };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of pts) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }
  return {
    x: minX,
    y: minY,
    width: Math.max(0, maxX - minX),
    height: Math.max(0, maxY - minY),
  };
}

/** Expand axis-aligned bbox by padding (px + ratio of span). */
export function expandBbox(bbox, paddingPx = RICH_RENDER.MIN_BBOX_PADDING_PX, paddingRatio = RICH_RENDER.BBOX_PADDING_RATIO) {
  if (!bbox || bbox.width <= 0 || bbox.height <= 0) {
    return { x: bbox?.x || 0, y: bbox?.y || 0, width: 0, height: 0 };
  }
  const padX = Math.max(paddingPx, bbox.width * paddingRatio);
  const padY = Math.max(paddingPx, bbox.height * paddingRatio);
  return {
    x: bbox.x - padX,
    y: bbox.y - padY,
    width: bbox.width + padX * 2,
    height: bbox.height + padY * 2,
  };
}

export function isValidJoint(u, v) {
  return Number.isFinite(u) && Number.isFinite(v) && (u > 1 || v > 1);
}

/** Map flat kp2d (42 floats) to canvas points. */
export function scalePointsToCanvas(flat, scaleX, scaleY) {
  const pts = [];
  for (let j = 0; j < 21; j += 1) {
    const u = flat[j * 2];
    const v = flat[j * 2 + 1];
    pts.push({
      x: u * scaleX,
      y: v * scaleY,
      ok: isValidJoint(u, v),
      index: j,
    });
  }
  return pts;
}

function validCanvasPoints(pts) {
  return (pts || []).filter((p) => p && p.ok && Number.isFinite(p.x) && Number.isFinite(p.y));
}

function rgbaString(color, alphaScale = 1) {
  const [r, g, b, a = 1] = color;
  const alpha = Math.max(0, Math.min(1, a * alphaScale));
  return `rgba(${r},${g},${b},${alpha})`;
}

/**
 * Build SenseXperience-style draw plan for one hand.
 * @returns {{ side, handId, alpha, maskPath, bbox, edges, joints, labelPos, labelText, styles }}
 */
export function buildRichHandDrawPlan(pts, side, scale = 1, alpha = 0.95, handId = null) {
  const valid = validCanvasPoints(pts);
  const hull = convexHull(valid);
  const rawBbox = bboxFromPoints(valid.length ? valid : hull);
  const bbox = expandBbox(rawBbox);
  const maskPath = hull.length >= 3 ? hull : [
    { x: bbox.x, y: bbox.y },
    { x: bbox.x + bbox.width, y: bbox.y },
    { x: bbox.x + bbox.width, y: bbox.y + bbox.height },
    { x: bbox.x, y: bbox.y + bbox.height },
  ];

  const edges = [];
  for (const [aIdx, bIdx] of HAND_EDGES) {
    const a = pts[aIdx];
    const b = pts[bIdx];
    if (!a?.ok || !b?.ok) continue;
    edges.push({
      from: { x: a.x, y: a.y },
      to: { x: b.x, y: b.y },
    });
  }

  const jointRadius = RICH_RENDER.JOINT_RADIUS * scale;
  const joints = [];
  for (let j = 0; j < 21; j += 1) {
    const p = pts[j];
    if (!p?.ok) continue;
    joints.push({
      x: p.x,
      y: p.y,
      color: FINGER_JOINT_COLORS[j] || BONE_COLOR,
      radius: jointRadius,
    });
  }

  const resolvedHandId = handId != null ? handId : (side === "left" ? 1 : 2);
  const labelText = HAND_ID_LABELS[side] || `ID:${resolvedHandId}`;
  const labelPos = {
    x: bbox.x,
    y: Math.max(0, bbox.y - 2 * scale),
  };

  const maskColor = HAND_MASK_COLORS[side] || HAND_MASK_COLORS.right;
  const bboxStroke = maskColor.slice(0, 3);

  return {
    side,
    handId: resolvedHandId,
    alpha,
    maskPath,
    bbox,
    edges,
    joints,
    labelPos,
    labelText,
    styles: {
      maskFill: rgbaString(maskColor, alpha),
      bboxStroke: rgbaString([...bboxStroke, 1], alpha),
      boneColor: rgbaString(BONE_COLOR, alpha * 0.95),
      boneLineWidth: RICH_RENDER.BONE_LINE_WIDTH * scale,
      bboxLineWidth: RICH_RENDER.BBOX_LINE_WIDTH * scale,
      jointRadius,
      labelFont: `bold ${Math.round(RICH_RENDER.LABEL_FONT_SIZE * scale)}px sans-serif`,
      labelTextColor: "#ffffff",
      labelBackground: rgbaString(bboxStroke, Math.min(1, alpha * 0.9)),
    },
  };
}

/** Execute draw plan on a 2D canvas context (mask → bbox → bones → joints → label). */
export function drawRichHandPlan(ctx, plan) {
  if (!ctx || !plan) return;

  const { styles } = plan;
  const alpha = Number.isFinite(plan.alpha) ? plan.alpha : 0.95;

  ctx.save();
  ctx.globalAlpha = alpha;

  if (plan.maskPath?.length >= 3) {
    ctx.beginPath();
    ctx.moveTo(plan.maskPath[0].x, plan.maskPath[0].y);
    for (let i = 1; i < plan.maskPath.length; i += 1) {
      ctx.lineTo(plan.maskPath[i].x, plan.maskPath[i].y);
    }
    ctx.closePath();
    ctx.fillStyle = styles.maskFill;
    ctx.fill();
  }

  const bb = plan.bbox;
  if (bb && bb.width > 0 && bb.height > 0) {
    ctx.strokeStyle = styles.bboxStroke;
    ctx.lineWidth = styles.bboxLineWidth;
    ctx.strokeRect(bb.x, bb.y, bb.width, bb.height);
  }

  ctx.strokeStyle = styles.boneColor;
  ctx.lineWidth = styles.boneLineWidth;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const edge of plan.edges || []) {
    ctx.beginPath();
    ctx.moveTo(edge.from.x, edge.from.y);
    ctx.lineTo(edge.to.x, edge.to.y);
    ctx.stroke();
  }

  for (const joint of plan.joints || []) {
    const [r, g, b] = joint.color || BONE_COLOR;
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.beginPath();
    ctx.arc(joint.x, joint.y, joint.radius, 0, Math.PI * 2);
    ctx.fill();
  }

  if (plan.labelText) {
    const pad = 3;
    ctx.font = styles.labelFont;
    const metrics = ctx.measureText(plan.labelText);
    const textW = metrics.width;
    const textH = RICH_RENDER.LABEL_FONT_SIZE;
    const bx = plan.labelPos.x;
    const by = Math.max(0, plan.labelPos.y - textH - pad);
    ctx.fillStyle = styles.labelBackground;
    ctx.fillRect(bx, by, textW + pad * 2, textH + pad);
    ctx.fillStyle = styles.labelTextColor;
    ctx.fillText(plan.labelText, bx + pad, by + textH);
  }

  ctx.restore();
}
