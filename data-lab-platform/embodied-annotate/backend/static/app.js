/* LeRobot Annotate – Path A: Label Studio–style timeline UI */

const laI18n = window.LA_I18N || {
  t: (key) => key,
  IS_DATALAB_EMBED: false,
  init() {},
};
const t = (...args) => laI18n.t(...args);

const SUBTASK_LABELS = [
  { id: 'idle', order: 0, color: '#9CA3AF', hint: '空闲/等待，手未参与任务' },
  { id: 'reach', order: 1, color: '#34D399', hint: '接近目标，尚未接触' },
  { id: 'pre_grasp', order: 2, color: '#6EE7B7', hint: '对准、张开，准备抓取' },
  { id: 'contact', order: 3, color: '#FB923C', hint: '刚触碰物体至抓稳前' },
  { id: 'lift', order: 4, color: '#60A5FA', hint: '物体离开支撑面' },
  { id: 'transport', order: 5, color: '#3B82F6', hint: '拿着物体水平移动' },
  { id: 'place', order: 6, color: '#A78BFA', hint: '朝放置位下降' },
  { id: 'release', order: 7, color: '#F472B6', hint: '松手，物体脱离' },
];

const LABEL_MAP = Object.fromEntries(SUBTASK_LABELS.map((l) => [l.id, l]));

const GAP_WARN_FRAMES = 10;

// ---------------------------------------------------------------------------
// Push to Hub (unchanged backend contract)
// ---------------------------------------------------------------------------

function showPushStatus(type, message, url = null) {
  const statusEl = document.getElementById('pushHubStatus');
  if (!statusEl) {
    alert(`${type}: ${message}`);
    return;
  }
  statusEl.className = 'ls-helper';
  if (type === 'loading') {
    statusEl.innerHTML = `<span class="spinner"></span> ${message}`;
  } else if (type === 'success') {
    statusEl.innerHTML = `
      <div class="status-box status-success">
        <div><strong>Success!</strong><p>${message}</p>
        ${url ? `<a href="${url}" target="_blank" class="status-link">View on Hugging Face Hub →</a>` : ''}</div>
      </div>`;
  } else if (type === 'error') {
    statusEl.innerHTML = `
      <div class="status-box status-error">
        <div><strong>Error</strong><p>${message}</p></div>
      </div>`;
  }
}

async function handlePushToHub() {
  const tokenEl = document.getElementById('hfToken');
  const btnEl = document.getElementById('pushHubBtn');
  const inPlaceEl = document.getElementById('pushInPlace');
  const newRepoEl = document.getElementById('newRepoId');
  const privateEl = document.getElementById('privateRepo');
  const msgEl = document.getElementById('commitMessage');

  if (!tokenEl) return;

  const token = tokenEl.value.trim();
  if (!token) {
    showPushStatus('error', 'Please enter your Hugging Face token');
    return;
  }

  const pushInPlaceChecked = inPlaceEl ? inPlaceEl.checked : true;
  const newRepoIdValue = newRepoEl ? newRepoEl.value.trim() : '';
  if (!pushInPlaceChecked && !newRepoIdValue) {
    showPushStatus('error', 'Please enter a new repo ID or check "Push in place"');
    return;
  }

  showPushStatus('loading', 'Pushing to Hub... This may take a while for large datasets.');
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.innerHTML = '<span class="spinner"></span> Pushing...';
  }

  try {
    const payload = {
      hf_token: token,
      push_in_place: pushInPlaceChecked,
      new_repo_id: pushInPlaceChecked ? null : newRepoIdValue,
      private: privateEl ? privateEl.checked : false,
      commit_message: (msgEl ? msgEl.value.trim() : '') || 'Add annotations from LeRobot Annotate',
    };
    const res = await fetch('/api/push_to_hub', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      showPushStatus('success', data.message, data.url);
    } else {
      showPushStatus('error', data.detail || 'Push failed.');
    }
  } catch (err) {
    showPushStatus('error', `Network error: ${err.message}`);
  } finally {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = 'Push to Hub';
    }
  }
}

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const statusEl = document.getElementById('status');
const connectForm = document.getElementById('connectForm');
const connectToggle = document.getElementById('connectToggle');
const sourceSelect = document.getElementById('sourceSelect');
const repoInput = document.getElementById('repoInput');
const localInput = document.getElementById('localInput');
const revisionInput = document.getElementById('revisionInput');
const videoKeySelect = document.getElementById('videoKeySelect');
const connectHelper = document.getElementById('connectHelper');
const repoLabel = document.getElementById('repoLabel');
const localLabel = document.getElementById('localLabel');

const workspace = document.getElementById('workspace');
const episodeList = document.getElementById('episodeList');
const episodeSearch = document.getElementById('episodeSearch');
const episodeTitle = document.getElementById('episodeTitle');
const episodeMeta = document.getElementById('episodeMeta');
const episodeTaskPanel = document.getElementById('episodeTaskPanel');
const episodeTaskText = document.getElementById('episodeTaskText');
const episodeVideo = document.getElementById('episodeVideo');
const frameReadout = document.getElementById('frameReadout');

const saveEpisodeBottom = document.getElementById('saveEpisodeBottom');
const saveStatusText = document.getElementById('saveStatusText');
const outcomeGroup = document.getElementById('outcomeGroup');
const outcomeRadios = outcomeGroup
  ? Array.from(outcomeGroup.querySelectorAll('input[type="radio"]'))
  : [];
const validationPanel = document.getElementById('validationPanel');
const validationErrors = document.getElementById('validationErrors');
const validationWarnings = document.getElementById('validationWarnings');
const validationDismiss = document.getElementById('validationDismiss');
const validationForceSave = document.getElementById('validationForceSave');
const resetEpisodeBtn = document.getElementById('resetEpisode');

const labelPalette = document.getElementById('labelPalette');
const labelCount = document.getElementById('labelCount');
const labelHint = document.getElementById('labelHint');
const timelineRuler = document.getElementById('timelineRuler');
const timelineTrack = document.getElementById('timelineTrack');
const timelineRegions = document.getElementById('timelineRegions');
const playhead = document.getElementById('playhead');
const dragPreview = document.getElementById('dragPreview');
const regionList = document.getElementById('regionList');
const regionCount = document.getElementById('regionCount');

const hlStart = document.getElementById('hlStart');
const hlEnd = document.getElementById('hlEnd');
const hlUser = document.getElementById('hlUser');
const hlRobot = document.getElementById('hlRobot');
const hlSetStart = document.getElementById('hlSetStart');
const hlSetEnd = document.getElementById('hlSetEnd');
const addHighLevel = document.getElementById('addHighLevel');
const highLevelList = document.getElementById('highLevelList');

const exportBtn = document.getElementById('exportBtn');
const outputDir = document.getElementById('outputDir');
const copyVideos = document.getElementById('copyVideos');
const exportStatus = document.getElementById('exportStatus');

const pushInPlace = document.getElementById('pushInPlace');
const newRepoRow = document.getElementById('newRepoRow');
const pushHubBtn = document.getElementById('pushHubBtn');

const DEMO_LOCAL_DATASET =
  '/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/lerobot-annotate/data/pusht';

const state = {
  dataset: null,
  episodes: [],
  currentEpisode: null,
  currentEpisodeData: null,
  annotations: {},
  selectedLabel: SUBTASK_LABELS[0].id,
  selectedRegionIdx: null,
  drag: null,
  dirty: false,
  savedSnapshot: null,
  validationResult: null,
  conflictIndices: new Set(),
  forceSaveDespiteWarnings: false,
  egoMode: false,
  egoDatasetPath: null,
  egoFeatures: [],
  egoHandPoses: null,
  egoHandPosesByFrame: new Map(),
};

// ---------------------------------------------------------------------------
// Frame ↔ second conversion (backend uses seconds)
// ---------------------------------------------------------------------------

function getFps() {
  return state.dataset?.fps || 30;
}

function getTotalFrames() {
  if (state.currentEpisodeData?.length != null) {
    return state.currentEpisodeData.length;
  }
  const dur = episodeVideo.duration;
  if (!dur) return 0;
  return Math.max(1, Math.round(dur * getFps()));
}

function secondsToStartFrame(sec) {
  return Math.max(0, Math.round(sec * getFps()));
}

function secondsToEndFrame(sec) {
  return Math.max(0, Math.ceil(sec * getFps()) - 1);
}

function startFrameToSeconds(frame) {
  return frame / getFps();
}

function endFrameToSeconds(frame) {
  return (frame + 1) / getFps();
}

function segToFrames(seg) {
  return {
    startFrame: secondsToStartFrame(seg.start),
    endFrame: secondsToEndFrame(seg.end),
    label: seg.label,
  };
}

function framesToSeg(startFrame, endFrame, label) {
  const total = getTotalFrames();
  const s = Math.max(0, Math.min(startFrame, endFrame));
  const e = Math.min(Math.max(startFrame, endFrame), total - 1);
  return {
    start: startFrameToSeconds(s),
    end: endFrameToSeconds(e),
    label,
  };
}

function parseEgoUrlParams() {
  const params = new URLSearchParams(window.location.search);
  const ego = params.get('ego') === '1' || params.has('datasetPath');
  const datasetPath = params.get('datasetPath');
  const episodeIndex = Number(params.get('episodeIndex') || '0');
  return { ego, datasetPath, episodeIndex: Number.isFinite(episodeIndex) ? episodeIndex : 0 };
}

function egoSegmentsToTimelineSubtasks(segments) {
  return (segments || []).map((seg) =>
    framesToSeg(seg.start_frame, seg.end_frame, seg.subtask_name || 'subtask'),
  );
}

function timelineSubtasksToEgoPayload(subtasks) {
  return subtasks.map((seg, index) => {
    const frames = segToFrames(seg);
    return {
      start_frame: frames.startFrame,
      end_frame: frames.endFrame,
      subtask_index: index,
      subtask_name: seg.label,
    };
  });
}

function hasEgoFeature(prefix) {
  return (state.egoFeatures || []).some((f) => String(f).startsWith(prefix));
}

let egoHandCanvas = null;
let egoHandCtx = null;

function ensureEgoHandCanvas() {
  if (!episodeVideo || !episodeVideo.parentElement) return null;
  if (!egoHandCanvas) {
    egoHandCanvas = document.createElement('canvas');
    egoHandCanvas.id = 'egoHandOverlay';
    egoHandCanvas.style.position = 'absolute';
    egoHandCanvas.style.left = '0';
    egoHandCanvas.style.top = '0';
    egoHandCanvas.style.pointerEvents = 'none';
    egoHandCanvas.style.zIndex = '2';
    const wrap = episodeVideo.parentElement;
    if (wrap.style.position !== 'relative' && wrap.style.position !== 'absolute') {
      wrap.style.position = 'relative';
    }
    wrap.appendChild(egoHandCanvas);
    egoHandCtx = egoHandCanvas.getContext('2d');
  }
  return egoHandCtx;
}

function projectHandPoseTo2D(pose63, videoW, videoH) {
  const pts = [];
  const arr = pose63 || [];
  if (arr.length < 63) return pts;
  for (let j = 0; j < 21; j += 1) {
    const x = Number(arr[j * 3]);
    const y = Number(arr[j * 3 + 1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    if (Math.abs(x) < 1e-3 && Math.abs(y) < 1e-3) continue;
    const px = x <= 1.5 ? x * videoW : x;
    const py = y <= 1.5 ? y * videoH : y;
    pts.push([px, py]);
  }
  return pts;
}

function drawEgoHandOverlay() {
  if (!state.egoMode || !hasEgoFeature('observation.hand_pose_')) {
    if (egoHandCanvas) egoHandCanvas.style.display = 'none';
    return;
  }
  const ctx = ensureEgoHandCanvas();
  if (!ctx || !episodeVideo.videoWidth) return;
  const rect = episodeVideo.getBoundingClientRect();
  egoHandCanvas.width = rect.width;
  egoHandCanvas.height = rect.height;
  egoHandCanvas.style.width = `${rect.width}px`;
  egoHandCanvas.style.height = `${rect.height}px`;
  egoHandCanvas.style.display = 'block';
  ctx.clearRect(0, 0, egoHandCanvas.width, egoHandCanvas.height);
  const frame = secondsToStartFrame(episodeVideo.currentTime || 0);
  const poseRow = state.egoHandPosesByFrame.get(frame);
  if (!poseRow) return;
  const sx = rect.width / episodeVideo.videoWidth;
  const sy = rect.height / episodeVideo.videoHeight;
  const drawSide = (pose, color) => {
    const pts = projectHandPoseTo2D(pose, episodeVideo.videoWidth, episodeVideo.videoHeight);
    if (pts.length < 2) return;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(pts[0][0] * sx, pts[0][1] * sy);
    for (let i = 1; i < pts.length; i += 1) {
      ctx.lineTo(pts[i][0] * sx, pts[i][1] * sy);
    }
    ctx.stroke();
    pts.forEach(([x, y]) => {
      ctx.beginPath();
      ctx.arc(x * sx, y * sy, 3, 0, Math.PI * 2);
      ctx.fill();
    });
  };
  drawSide(poseRow.hand_pose_left, '#34d399');
  drawSide(poseRow.hand_pose_right, '#60a5fa');
}

async function loadEgoContext(epIdx) {
  if (!state.egoMode || !state.egoDatasetPath) return null;
  const url = `/api/ego/load?datasetPath=${encodeURIComponent(state.egoDatasetPath)}&episodeIndex=${epIdx}`;
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Failed to load EGO dataset');
  state.egoFeatures = data.features || [];
  if (hasEgoFeature('observation.hand_pose_')) {
    const hpRes = await fetch(
      `/api/ego/hand_poses?datasetPath=${encodeURIComponent(state.egoDatasetPath)}&episodeIndex=${epIdx}`,
    );
    const hpData = await hpRes.json();
    if (hpRes.ok) {
      state.egoHandPosesByFrame = new Map(
        (hpData.poses || []).map((row) => [row.frame_index, row]),
      );
    }
  } else {
    state.egoHandPosesByFrame = new Map();
  }
  return data;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setStatus(text, ok = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle('ok', ok);
}

function setHelper(el, message, ok = false) {
  if (!el) return;
  el.textContent = message;
  el.classList.toggle('ok', ok);
  el.classList.toggle('error', !ok && message && !message.includes('...'));
}

function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return '';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}m ${secs}s`;
}

function currentTime() {
  return Number(episodeVideo.currentTime.toFixed(3));
}

function currentFrame() {
  return secondsToStartFrame(episodeVideo.currentTime);
}

function getEpisodeDuration() {
  return episodeVideo.duration || 0;
}

function getEpisodeAnnotations(epIdx) {
  if (!state.annotations[epIdx]) {
    state.annotations[epIdx] = { subtasks: [], high_levels: [], outcome: null };
  }
  return state.annotations[epIdx];
}

function getAnnotationsSnapshot(epIdx) {
  const ann = getEpisodeAnnotations(epIdx);
  const sorted = [...ann.subtasks].sort((a, b) => a.start - b.start);
  return JSON.stringify({ subtasks: sorted, outcome: ann.outcome ?? null });
}

function getSubtasksSnapshot(epIdx) {
  return getAnnotationsSnapshot(epIdx);
}

function markDirty() {
  if (state.currentEpisode == null) return;
  state.dirty = true;
  state.forceSaveDespiteWarnings = false;
  hideValidationPanel();
  updateSaveUI();
}

function clearDirty() {
  if (state.currentEpisode != null) {
    state.savedSnapshot = getAnnotationsSnapshot(state.currentEpisode);
  }
  state.dirty = false;
  updateSaveUI();
}

function renderTaskPanel(ep) {
  if (!episodeTaskPanel || !episodeTaskText) return;
  const text = ep?.task_text?.trim();
  if (text) {
    episodeTaskText.textContent = text;
    episodeTaskPanel.hidden = false;
  } else {
    episodeTaskText.textContent = '';
    episodeTaskPanel.hidden = true;
  }
}

function getSelectedOutcome() {
  const checked = outcomeRadios.find((r) => r.checked);
  return checked ? checked.value : null;
}

function setOutcomeUI(outcome) {
  outcomeRadios.forEach((radio) => {
    radio.checked = outcome === radio.value;
  });
}

function bindOutcomeRadios() {
  outcomeRadios.forEach((radio) => {
    let wasChecked = false;
    radio.addEventListener('mousedown', () => {
      wasChecked = radio.checked;
    });
    radio.addEventListener('click', () => {
      if (state.currentEpisode == null) return;
      const ann = getEpisodeAnnotations(state.currentEpisode);
      if (wasChecked) {
        radio.checked = false;
        ann.outcome = null;
      } else {
        ann.outcome = radio.value;
      }
      markDirty();
    });
  });
}

function updateSaveUI() {
  const hasEpisode = state.currentEpisode != null;
  const label = t('save_button');

  if (saveEpisodeBottom) {
    saveEpisodeBottom.disabled = !hasEpisode;
    saveEpisodeBottom.textContent = label;
    saveEpisodeBottom.classList.toggle('dirty-pulse', state.dirty && hasEpisode);
  }

  if (saveStatusText) {
    saveStatusText.textContent = state.dirty ? t('save_dirty') : t('save_ok');
    saveStatusText.classList.toggle('dirty', state.dirty);
    saveStatusText.classList.toggle('ok', !state.dirty);
  }
}

function confirmLeaveIfDirty() {
  if (!state.dirty) return true;
  return window.confirm(t('confirm_leave'));
}

function handleSaveClick() {
  runValidationAndSave();
}

// ---------------------------------------------------------------------------
// Region validation (P0-2, frontend only)
// ---------------------------------------------------------------------------

function getSegmentFramesList(epIdx) {
  const ann = getEpisodeAnnotations(epIdx);
  return ann.subtasks.map((seg, index) => ({
    index,
    label: seg.label,
    ...segToFrames(seg),
  }));
}

function segmentsOverlap(a, b) {
  return a.startFrame <= b.endFrame && b.startFrame <= a.endFrame;
}

function validateRegions(epIdx) {
  const errors = [];
  const warnings = [];
  const total = getTotalFrames();
  const maxFrame = total > 0 ? total - 1 : 0;
  const segments = getSegmentFramesList(epIdx);

  segments.forEach((seg) => {
    if (seg.startFrame > seg.endFrame) {
      errors.push({
        code: 'V-02',
        message: `${seg.label}：起始帧 ${seg.startFrame} 大于结束帧 ${seg.endFrame}`,
        segIndex: seg.index,
      });
    }
    if (seg.startFrame < 0 || seg.endFrame > maxFrame) {
      errors.push({
        code: 'V-03',
        message: `${seg.label}：帧范围 [${seg.startFrame}, ${seg.endFrame}] 超出 [0, ${maxFrame}]`,
        segIndex: seg.index,
      });
    }
  });

  const sorted = [...segments].sort((a, b) => a.startFrame - b.startFrame || a.index - b.index);

  for (let i = 0; i < sorted.length; i += 1) {
    for (let j = i + 1; j < sorted.length; j += 1) {
      const a = sorted[i];
      const b = sorted[j];
      if (segmentsOverlap(a, b)) {
        errors.push({
          code: 'V-01',
          message: `重叠：${a.label} f${a.startFrame}–f${a.endFrame} 与 ${b.label} f${b.startFrame}–f${b.endFrame}`,
          segIndexA: a.index,
          segIndexB: b.index,
        });
      }
    }
  }

  for (let i = 0; i < sorted.length - 1; i += 1) {
    const gap = sorted[i + 1].startFrame - sorted[i].endFrame - 1;
    if (gap > GAP_WARN_FRAMES) {
      warnings.push({
        code: 'V-04',
        message: `帧 ${sorted[i].endFrame + 1}–${sorted[i + 1].startFrame - 1} 连续空隙 ${gap} 帧（超过 ${GAP_WARN_FRAMES} 帧）`,
      });
    }
  }

  if (sorted.length > 0 && total > 0) {
    if (sorted[0].startFrame > 0) {
      warnings.push({
        code: 'V-05',
        message: `首部未覆盖：帧 0–${sorted[0].startFrame - 1} 未标注`,
      });
    }
    const last = sorted[sorted.length - 1];
    if (last.endFrame < maxFrame) {
      warnings.push({
        code: 'V-05',
        message: `尾部未覆盖：帧 ${last.endFrame + 1}–${maxFrame} 未标注`,
      });
    }
  }

  return { errors, warnings };
}

function getConflictIndices(result) {
  const indices = new Set();
  result.errors.forEach((issue) => {
    if (issue.segIndex != null) indices.add(issue.segIndex);
    if (issue.segIndexA != null) indices.add(issue.segIndexA);
    if (issue.segIndexB != null) indices.add(issue.segIndexB);
  });
  return indices;
}

function hideValidationPanel() {
  if (!validationPanel) return;
  validationPanel.hidden = true;
  validationPanel.classList.remove('has-errors', 'has-warnings');
  if (validationErrors) validationErrors.innerHTML = '';
  if (validationWarnings) validationWarnings.innerHTML = '';
  if (validationForceSave) validationForceSave.hidden = true;
  state.conflictIndices = new Set();
}

function renderValidationPanel(result) {
  if (!validationPanel) return;
  const hasErrors = result.errors.length > 0;
  const hasWarnings = result.warnings.length > 0;

  if (!hasErrors && !hasWarnings) {
    hideValidationPanel();
    return;
  }

  validationPanel.hidden = false;
  validationPanel.classList.toggle('has-errors', hasErrors);
  validationPanel.classList.toggle('has-warnings', !hasErrors && hasWarnings);

  if (validationErrors) {
    validationErrors.innerHTML = hasErrors
      ? `<strong>${t('validation_cannot_save')}</strong><ul>${result.errors.map((e) => `<li>${e.message}</li>`).join('')}</ul>`
      : '';
  }

  if (validationWarnings) {
    validationWarnings.innerHTML = hasWarnings
      ? `<strong>${t('validation_warnings')}</strong><ul>${result.warnings.map((w) => `<li>${w.message}</li>`).join('')}</ul>`
      : '';
  }

  if (validationForceSave) {
    validationForceSave.hidden = hasErrors || !hasWarnings;
  }
}

function wouldOverlapNewSegment(startFrame, endFrame, subtasks) {
  const candidate = { startFrame, endFrame };
  return subtasks.some((seg) => segmentsOverlap(candidate, segToFrames(seg)));
}

async function runValidationAndSave() {
  if (state.currentEpisode == null) return;

  const result = validateRegions(state.currentEpisode);
  state.validationResult = result;
  state.conflictIndices = getConflictIndices(result);
  renderValidationPanel(result);
  renderAllTimeline();

  if (result.errors.length > 0) return;
  if (result.warnings.length > 0 && !state.forceSaveDespiteWarnings) return;

  state.forceSaveDespiteWarnings = false;
  await saveEpisode();
  hideValidationPanel();
}

function frameToPercent(frame) {
  const total = getTotalFrames();
  if (!total) return 0;
  return (frame / total) * 100;
}

function percentToFrame(pct) {
  const total = getTotalFrames();
  return Math.round(Math.max(0, Math.min(1, pct)) * (total - 1));
}

function trackXToPercent(clientX) {
  const rect = timelineTrack.getBoundingClientRect();
  const x = clientX - rect.left;
  return Math.max(0, Math.min(1, x / rect.width));
}

// ---------------------------------------------------------------------------
// Label palette
// ---------------------------------------------------------------------------

function isStandardLabel(label) {
  return Boolean(LABEL_MAP[label]);
}

function updateLabelHintForSelection(labelId) {
  if (!labelHint) return;
  const lbl = LABEL_MAP[labelId];
  labelHint.textContent = lbl
    ? t('label_hint_selected', { id: lbl.id, hint: lbl.hint })
    : t('label_hint_default');
  labelHint.classList.remove('error');
}

function renderLabelPalette() {
  if (!labelPalette) return;
  labelPalette.innerHTML = '';
  if (labelCount) labelCount.textContent = t('label_count', { n: SUBTASK_LABELS.length });

  [...SUBTASK_LABELS].sort((a, b) => a.order - b.order).forEach((lbl) => {
    const tag = document.createElement('span');
    tag.className = 'ls-label-tag';
    tag.textContent = lbl.id;
    tag.dataset.label = lbl.id;
    tag.style.backgroundColor = lbl.color;
    tag.title = lbl.hint;
    tag.setAttribute('role', 'option');
    tag.setAttribute('aria-label', `${lbl.id}: ${lbl.hint}`);
    tag.setAttribute('aria-selected', lbl.id === state.selectedLabel ? 'true' : 'false');
    tag.tabIndex = 0;
    if (lbl.id === state.selectedLabel) tag.classList.add('selected');

    const selectLabel = () => {
      state.selectedLabel = lbl.id;
      renderLabelPalette();
      updateLabelHintForSelection(lbl.id);
    };

    tag.addEventListener('click', selectLabel);
    tag.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        selectLabel();
      }
    });

    labelPalette.appendChild(tag);
  });
}

// ---------------------------------------------------------------------------
// Timeline rendering
// ---------------------------------------------------------------------------

function renderRuler() {
  timelineRuler.innerHTML = '';
  const total = getTotalFrames();
  if (!total) return;
  const fps = getFps();
  const duration = getEpisodeDuration() || total / fps;
  const tickCount = Math.min(10, Math.max(4, Math.floor(duration)));
  for (let i = 0; i <= tickCount; i++) {
    const tick = document.createElement('span');
    tick.className = 'ls-ruler-tick';
    const pct = (i / tickCount) * 100;
    tick.style.left = `${pct}%`;
    const t = (duration * i) / tickCount;
    tick.textContent = `${Math.round(t * fps)}f`;
    timelineRuler.appendChild(tick);
  }
}

function renderTimelineRegions() {
  timelineRegions.innerHTML = '';
  if (state.currentEpisode == null) return;
  const ann = getEpisodeAnnotations(state.currentEpisode);
  const segments = ann.subtasks.map((seg, idx) => ({ seg, idx }));
  segments.sort((a, b) => a.seg.start - b.seg.start);

  segments.forEach(({ seg, idx }) => {
    const { startFrame, endFrame } = segToFrames(seg);
    const bar = document.createElement('div');
    bar.className = 'ls-region-bar';
    const color = LABEL_MAP[seg.label]?.color || '#576cc9';
    bar.style.setProperty('--region-color', color);
    bar.style.left = `${frameToPercent(startFrame)}%`;
    bar.style.width = `${Math.max(frameToPercent(endFrame + 1) - frameToPercent(startFrame), 0.5)}%`;
    bar.textContent = seg.label;
    const hint = LABEL_MAP[seg.label]?.hint;
    bar.title = hint
      ? `${seg.label}（${hint}）: f${startFrame}–f${endFrame}`
      : `${seg.label}: f${startFrame}–f${endFrame}`;
    if (!isStandardLabel(seg.label)) bar.classList.add('nonstandard');
    if (state.selectedRegionIdx === idx) bar.classList.add('selected');
    if (state.conflictIndices.has(idx)) bar.classList.add('conflict');
    bar.addEventListener('click', (e) => {
      e.stopPropagation();
      state.selectedRegionIdx = idx;
      renderTimelineRegions();
      renderRegionList();
    });
    timelineRegions.appendChild(bar);
  });
}

function updatePlayhead() {
  const total = getTotalFrames();
  if (!total || !episodeVideo.duration) {
    playhead.style.left = '0%';
    return;
  }
  const frame = currentFrame();
  playhead.style.left = `${frameToPercent(frame)}%`;
  frameReadout.textContent = t('frame_readout', { cur: frame, max: total - 1 });
}

function renderAllTimeline() {
  renderRuler();
  renderTimelineRegions();
  updatePlayhead();
  renderRegionList();
}

// ---------------------------------------------------------------------------
// Region list (frame-based editing)
// ---------------------------------------------------------------------------

function renderRegionList() {
  regionList.innerHTML = '';
  if (state.currentEpisode == null) return;
  const ann = getEpisodeAnnotations(state.currentEpisode);
  const segments = ann.subtasks.map((seg, idx) => ({ seg, idx }));
  segments.sort((a, b) => a.seg.start - b.seg.start);
  regionCount.textContent = String(ann.subtasks.length);

  segments.forEach(({ seg, idx }) => {
    const { startFrame, endFrame } = segToFrames(seg);
    const row = document.createElement('div');
    row.className = 'ls-region-row';
    if (state.selectedRegionIdx === idx) row.style.borderColor = 'var(--ls-primary)';
    if (state.conflictIndices.has(idx)) row.classList.add('conflict');

    const swatch = document.createElement('span');
    swatch.className = 'ls-region-swatch';
    swatch.style.background = LABEL_MAP[seg.label]?.color || '#576cc9';

    const startInput = document.createElement('input');
    startInput.type = 'number';
    startInput.min = '0';
    startInput.value = startFrame;
    startInput.title = 'Start frame';
    startInput.addEventListener('change', () => {
      const nf = Number(startInput.value);
      const ef = secondsToEndFrame(seg.end);
      if (!Number.isNaN(nf)) {
        Object.assign(seg, framesToSeg(nf, ef, seg.label));
        markDirty();
        renderAllTimeline();
      }
    });

    const endInput = document.createElement('input');
    endInput.type = 'number';
    endInput.min = '0';
    endInput.value = endFrame;
    endInput.title = 'End frame';
    endInput.addEventListener('change', () => {
      const sf = secondsToStartFrame(seg.start);
      const nf = Number(endInput.value);
      if (!Number.isNaN(nf)) {
        Object.assign(seg, framesToSeg(sf, nf, seg.label));
        markDirty();
        renderAllTimeline();
      }
    });

    const labelSpan = document.createElement('span');
    labelSpan.className = 'ls-region-label';
    labelSpan.textContent = seg.label;
    if (!isStandardLabel(seg.label)) {
      const warn = document.createElement('span');
      warn.className = 'ls-label-nonstandard';
      warn.textContent = t('non_standard_badge');
      warn.title = '该标签不在 8 类固定枚举内，保存时将原样提交';
      labelSpan.appendChild(warn);
    }

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'ls-btn ls-btn-ghost';
    deleteBtn.textContent = '×';
    deleteBtn.title = 'Delete region';
    deleteBtn.addEventListener('click', () => {
      ann.subtasks.splice(idx, 1);
      state.selectedRegionIdx = null;
      markDirty();
      renderAllTimeline();
    });

    row.appendChild(swatch);
    row.appendChild(startInput);
    row.appendChild(endInput);
    row.appendChild(labelSpan);
    row.appendChild(deleteBtn);
    regionList.appendChild(row);
  });
}

// ---------------------------------------------------------------------------
// Timeline drag interaction
// ---------------------------------------------------------------------------

function showDragPreview(startPct, endPct) {
  const left = Math.min(startPct, endPct) * 100;
  const width = Math.abs(endPct - startPct) * 100;
  const dragColor = LABEL_MAP[state.selectedLabel]?.color || '#576cc9';
  dragPreview.hidden = false;
  dragPreview.style.left = `${left}%`;
  dragPreview.style.width = `${width}%`;
  dragPreview.style.setProperty('--drag-color', dragColor);
}

function hideDragPreview() {
  dragPreview.hidden = true;
}

function onTimelineMouseDown(e) {
  if (state.currentEpisode == null || e.button !== 0) return;
  if (!state.selectedLabel || !isStandardLabel(state.selectedLabel)) {
    labelHint.textContent = t('label_hint_pick');
    labelHint.classList.add('error');
    return;
  }
  const pct = trackXToPercent(e.clientX);
  state.drag = { startPct: pct, currentPct: pct, moved: false };
  showDragPreview(pct, pct);
  e.preventDefault();
}

function onTimelineMouseMove(e) {
  if (!state.drag) return;
  const pct = trackXToPercent(e.clientX);
  if (Math.abs(pct - state.drag.startPct) > 0.005) {
    state.drag.moved = true;
  }
  state.drag.currentPct = pct;
  showDragPreview(state.drag.startPct, pct);
}

function onTimelineMouseUp(e) {
  if (!state.drag) return;
  const { startPct, currentPct, moved } = state.drag;
  state.drag = null;
  hideDragPreview();

  if (!moved) {
    const frame = percentToFrame(startPct);
    episodeVideo.currentTime = startFrameToSeconds(frame);
    updatePlayhead();
    return;
  }

  const f0 = percentToFrame(Math.min(startPct, currentPct));
  const f1 = percentToFrame(Math.max(startPct, currentPct));
  if (f1 <= f0) return;

  const ann = getEpisodeAnnotations(state.currentEpisode);
  if (wouldOverlapNewSegment(f0, f1, ann.subtasks)) {
    labelHint.textContent = t('label_hint_overlap');
    labelHint.classList.add('error');
    return;
  }
  labelHint.classList.remove('error');

  ann.subtasks.push(framesToSeg(f0, f1, state.selectedLabel));
  state.selectedRegionIdx = ann.subtasks.length - 1;
  markDirty();
  renderAllTimeline();
}

function bindTimelineEvents() {
  timelineTrack.addEventListener('mousedown', onTimelineMouseDown);
  window.addEventListener('mousemove', onTimelineMouseMove);
  window.addEventListener('mouseup', onTimelineMouseUp);
}

// ---------------------------------------------------------------------------
// Episodes
// ---------------------------------------------------------------------------

function renderEpisodes() {
  episodeList.innerHTML = '';
  const query = episodeSearch.value.trim();
  const filtered = state.episodes.filter((ep) => ep.episode_index.toString().includes(query));
  filtered.forEach((ep) => {
    const li = document.createElement('li');
    li.textContent = `Episode ${ep.episode_index}`;
    const span = document.createElement('span');
    span.textContent = formatDuration(ep.duration);
    li.appendChild(span);
    if (state.currentEpisode === ep.episode_index) li.classList.add('active');
    li.addEventListener('click', () => requestSelectEpisode(ep.episode_index));
    episodeList.appendChild(li);
  });
}

function renderHighLevels() {
  highLevelList.innerHTML = '';
  if (state.currentEpisode == null) return;
  const ann = getEpisodeAnnotations(state.currentEpisode);
  ann.high_levels.sort((a, b) => a.start - b.start);

  ann.high_levels.forEach((seg, idx) => {
    const row = document.createElement('div');
    row.className = 'ls-region-row';

    const startInput = document.createElement('input');
    startInput.type = 'number';
    startInput.step = '0.001';
    startInput.value = seg.start;
    startInput.addEventListener('change', () => { seg.start = Number(startInput.value); });

    const endInput = document.createElement('input');
    endInput.type = 'number';
    endInput.step = '0.001';
    endInput.value = seg.end;
    endInput.addEventListener('change', () => { seg.end = Number(endInput.value); });

    const promptInput = document.createElement('input');
    promptInput.type = 'text';
    promptInput.value = seg.user_prompt;
    promptInput.placeholder = 'User prompt';
    promptInput.addEventListener('change', () => { seg.user_prompt = promptInput.value; });

    const robotInput = document.createElement('input');
    robotInput.type = 'text';
    robotInput.value = seg.robot_utterance;
    robotInput.placeholder = 'Robot response';
    robotInput.addEventListener('change', () => { seg.robot_utterance = robotInput.value; });

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'ls-btn ls-btn-ghost';
    deleteBtn.textContent = 'Delete';
    deleteBtn.addEventListener('click', () => {
      ann.high_levels.splice(idx, 1);
      renderHighLevels();
    });

    row.appendChild(startInput);
    row.appendChild(endInput);
    row.appendChild(promptInput);
    row.appendChild(robotInput);
    row.appendChild(deleteBtn);
    highLevelList.appendChild(row);
  });
}

async function requestSelectEpisode(epIdx) {
  if (
    state.currentEpisode !== null
    && state.currentEpisode !== epIdx
    && state.dirty
    && !confirmLeaveIfDirty()
  ) {
    return;
  }
  await selectEpisode(epIdx);
}

async function selectEpisode(epIdx) {
  state.currentEpisode = epIdx;
  state.selectedRegionIdx = null;
  state.forceSaveDespiteWarnings = false;
  hideValidationPanel();
  episodeTitle.textContent = `Episode ${epIdx}`;
  const ep = state.episodes.find((e) => e.episode_index === epIdx);
  state.currentEpisodeData = ep || null;
  episodeMeta.textContent = ep
    ? `${ep.length} frames @ ${getFps()} fps • ${formatDuration(ep.duration)}`
    : '';
  renderTaskPanel(ep);

  if (state.egoMode && state.egoDatasetPath) {
    try {
      const egoData = await loadEgoContext(epIdx);
      const subtasks = egoSegmentsToTimelineSubtasks(egoData?.segments || []);
      state.annotations[epIdx] = {
        subtasks,
        high_levels: [],
        outcome: null,
      };
    } catch (err) {
      setHelper(connectHelper, err.message);
      state.annotations[epIdx] = { subtasks: [], high_levels: [], outcome: null };
    }
  } else {
    const res = await fetch(`/api/episodes/${epIdx}/annotations`);
    const data = await res.json();
    state.annotations[epIdx] = {
      subtasks: data.subtasks || [],
      high_levels: data.high_levels || [],
      outcome: data.outcome ?? null,
    };
  }
  setOutcomeUI(state.annotations[epIdx].outcome);

  const videoUrl = `/api/video/${epIdx}?video_key=${encodeURIComponent(state.dataset.selected_video_key)}`;
  episodeVideo.src = videoUrl;

  if (saveEpisodeBottom) saveEpisodeBottom.disabled = false;
  resetEpisodeBtn.disabled = false;
  state.savedSnapshot = getAnnotationsSnapshot(epIdx);
  clearDirty();
  renderEpisodes();
  renderHighLevels();
  if (episodeVideo.readyState >= 1) {
    renderAllTimeline();
  }
}

async function saveEpisode() {
  if (state.currentEpisode == null) return;
  const ann = getEpisodeAnnotations(state.currentEpisode);

  if (state.egoMode && state.egoDatasetPath) {
    const payload = {
      dataset_path: state.egoDatasetPath,
      episode_index: state.currentEpisode,
      subtasks: timelineSubtasksToEgoPayload(ann.subtasks),
    };
    const res = await fetch('/api/ego/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      clearDirty();
      setStatus(t('status_saved'), true);
      setHelper(connectHelper, t('connect_episode_saved', { idx: state.currentEpisode }), true);
      if (saveStatusText) {
        saveStatusText.textContent = t('save_ok');
        saveStatusText.classList.remove('dirty');
        saveStatusText.classList.add('ok');
      }
      await loadEgoContext(state.currentEpisode);
    } else {
      setHelper(connectHelper, data.detail || t('save_failed'));
    }
    return;
  }

  const payload = {
    episode_index: state.currentEpisode,
    subtasks: ann.subtasks,
    high_levels: ann.high_levels,
  };
  if (ann.outcome) {
    payload.outcome = ann.outcome;
  }
  const res = await fetch(`/api/episodes/${state.currentEpisode}/annotations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    clearDirty();
    setStatus(t('status_saved'), true);
    setHelper(connectHelper, t('connect_episode_saved', { idx: state.currentEpisode }), true);
    if (saveStatusText) {
      saveStatusText.textContent = t('save_ok');
      saveStatusText.classList.remove('dirty');
      saveStatusText.classList.add('ok');
    }
    window.setTimeout(() => {
      if (!state.dirty && state.dataset) {
        setStatus(t('status_loaded', { name: state.dataset.repo_id || state.dataset.root }), true);
      }
    }, 2000);
  } else {
    const data = await res.json();
    setHelper(connectHelper, data.detail || t('save_failed'));
  }
}

// ---------------------------------------------------------------------------
// Connect / load
// ---------------------------------------------------------------------------

function updateConnectFields() {
  const isLocal = sourceSelect.value === 'local';
  if (repoLabel) repoLabel.style.display = isLocal ? 'none' : 'flex';
  if (localLabel) localLabel.style.display = isLocal ? 'flex' : 'none';
  const revisionLabel = document.getElementById('revisionLabel');
  if (revisionLabel) revisionLabel.style.display = isLocal ? 'none' : 'flex';
  if (isLocal && localInput && !localInput.value.trim()) {
    localInput.value = DEMO_LOCAL_DATASET;
  }
  setHelper(
    connectHelper,
    isLocal ? t('connect_helper_local') : t('connect_helper_hf'),
  );
}

function populateVideoKeys(keys, selected) {
  videoKeySelect.innerHTML = '';
  if (!keys) return;
  keys.forEach((key) => {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = key;
    if (key === selected) option.selected = true;
    videoKeySelect.appendChild(option);
  });
}

sourceSelect.addEventListener('change', updateConnectFields);

connectForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const isLocal = sourceSelect.value === 'local';
  const localPath = localInput.value.trim() || null;
  if (isLocal && !localPath) {
    setHelper(connectHelper, t('connect_local_required'));
    localInput.focus();
    return;
  }
  const payload = {
    source: sourceSelect.value,
    repo_id: isLocal ? null : repoInput.value.trim() || null,
    revision: revisionInput.value.trim() || null,
    local_path: isLocal ? localPath : null,
    video_key: videoKeySelect.value || null,
  };

  setHelper(connectHelper, t('connect_loading'));
  try {
    const res = await fetch('/api/dataset/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to load dataset');

    const egoParams = parseEgoUrlParams();
    state.egoMode = egoParams.ego && !!egoParams.datasetPath;
    state.egoDatasetPath = state.egoMode
      ? egoParams.datasetPath
      : (isLocal ? localPath : null);

    state.dataset = data;
    state.episodes = data.episodes || [];
    setStatus(t('status_loaded', { name: data.repo_id || data.root }), true);
    setHelper(connectHelper, t('connect_loaded', { count: state.episodes.length }), true);
    workspace.style.display = 'grid';

    const connectBody = connectForm;
    if (connectBody) connectBody.classList.add('collapsed');
    if (connectToggle) connectToggle.setAttribute('aria-expanded', 'false');

    populateVideoKeys(data.video_keys, data.selected_video_key);
    renderEpisodes();
    if (state.episodes.length > 0) {
      const startEp = state.egoMode ? (parseEgoUrlParams().episodeIndex || state.episodes[0].episode_index) : state.episodes[0].episode_index;
      await selectEpisode(startEp);
    }
  } catch (err) {
    setStatus(t('status_disconnected'));
    setHelper(connectHelper, err.message);
  }
});

if (connectToggle) {
  connectToggle.addEventListener('click', () => {
    const expanded = connectToggle.getAttribute('aria-expanded') === 'true';
    connectToggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
    connectForm.classList.toggle('collapsed', expanded);
  });
}

// ---------------------------------------------------------------------------
// High-level optional annotations
// ---------------------------------------------------------------------------

hlSetStart.addEventListener('click', () => { hlStart.value = currentTime(); });
hlSetEnd.addEventListener('click', () => { hlEnd.value = currentTime(); });

addHighLevel.addEventListener('click', () => {
  if (state.currentEpisode == null) return;
  const start = Number(hlStart.value);
  const end = Number(hlEnd.value);
  const userPrompt = hlUser.value.trim();
  const robotUtter = hlRobot.value.trim();
  if (!userPrompt || !robotUtter || Number.isNaN(start) || Number.isNaN(end) || end <= start) return;
  const ann = getEpisodeAnnotations(state.currentEpisode);
  ann.high_levels.push({
    start,
    end,
    user_prompt: userPrompt,
    robot_utterance: robotUtter,
    skill: null,
    scenario_type: null,
    response_type: null,
  });
  renderHighLevels();
  hlUser.value = '';
  hlRobot.value = '';
});

// ---------------------------------------------------------------------------
// Save / reset / export
// ---------------------------------------------------------------------------

function bindSaveButtons() {
  if (saveEpisodeBottom) saveEpisodeBottom.addEventListener('click', handleSaveClick);
}

bindSaveButtons();
bindOutcomeRadios();

if (validationDismiss) {
  validationDismiss.addEventListener('click', () => {
    state.forceSaveDespiteWarnings = false;
    hideValidationPanel();
    renderAllTimeline();
  });
}

if (validationForceSave) {
  validationForceSave.addEventListener('click', () => {
    state.forceSaveDespiteWarnings = true;
    runValidationAndSave();
  });
}

resetEpisodeBtn.addEventListener('click', () => {
  if (state.currentEpisode == null) return;
  state.annotations[state.currentEpisode] = { subtasks: [], high_levels: [], outcome: null };
  state.selectedRegionIdx = null;
  setOutcomeUI(null);
  markDirty();
  renderAllTimeline();
  renderHighLevels();
});

window.addEventListener('beforeunload', (event) => {
  if (state.dirty) {
    event.preventDefault();
    event.returnValue = '';
  }
});

episodeSearch.addEventListener('input', renderEpisodes);

episodeVideo.addEventListener('loadedmetadata', () => {
  renderAllTimeline();
  drawEgoHandOverlay();
});

episodeVideo.addEventListener('timeupdate', () => {
  updatePlayhead();
  drawEgoHandOverlay();
});

exportBtn.addEventListener('click', async () => {
  exportStatus.textContent = t('export_running');
  const payload = {
    output_dir: outputDir.value.trim() || null,
    copy_videos: copyVideos.checked,
  };
  const res = await fetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (res.ok) {
    setHelper(
      exportStatus,
      t('export_done', {
        dir: data.output_dir,
        subtasks: data.subtasks,
        hl: data.tasks_high_level,
      }),
      true,
    );
  } else {
    setHelper(exportStatus, data.detail || t('export_failed'));
  }
});

if (pushInPlace && newRepoRow) {
  pushInPlace.addEventListener('change', () => {
    newRepoRow.style.display = pushInPlace.checked ? 'none' : 'flex';
  });
  newRepoRow.style.display = pushInPlace.checked ? 'none' : 'flex';
}

if (pushHubBtn) pushHubBtn.addEventListener('click', handlePushToHub);

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

laI18n.init();

window.__laOnLocaleChange = () => {
  if (validationDismiss) validationDismiss.textContent = t('validation_back');
  if (validationForceSave) validationForceSave.textContent = t('validation_force_save');
  if (saveEpisodeBottom) saveEpisodeBottom.setAttribute('aria-label', t('save_button'));
  updateConnectFields();
  renderLabelPalette();
  updateLabelHintForSelection(state.selectedLabel);
  updateSaveUI();
};

window.__laOnLocaleChange();

workspace.style.display = 'none';
renderLabelPalette();
updateLabelHintForSelection(state.selectedLabel);
bindTimelineEvents();
updateConnectFields();
updateSaveUI();

(() => {
  const egoParams = parseEgoUrlParams();
  if (egoParams.datasetPath && localInput) {
    localInput.value = egoParams.datasetPath;
    if (sourceSelect) sourceSelect.value = 'local';
    updateConnectFields();
  }
})();
