/* LeRobot Annotate – Path A: Label Studio–style timeline UI */

const laI18n = window.LA_I18N || {
  t: (key) => key,
  IS_DATALAB_EMBED: false,
  init() {},
};
const t = (...args) => laI18n.t(...args);

const DEFAULT_ANNOTATION_SCHEMA = {
  schema_id: 'default_manipulation_v1',
  schema_version: 1,
  subtask_labels: [
    { id: 'idle', order: 0, color: '#9CA3AF', label_zh: '空闲', hint_zh: '空闲/等待，手未参与任务' },
    { id: 'reach', order: 1, color: '#34D399', label_zh: '接近', hint_zh: '接近目标，尚未接触' },
    { id: 'pre_grasp', order: 2, color: '#6EE7B7', label_zh: '预抓取', hint_zh: '对准、张开，准备抓取' },
    { id: 'contact', order: 3, color: '#FB923C', label_zh: '接触', hint_zh: '刚触碰物体至抓稳前' },
    { id: 'lift', order: 4, color: '#60A5FA', label_zh: '抬起', hint_zh: '物体离开支撑面' },
    { id: 'transport', order: 5, color: '#3B82F6', label_zh: '搬运', hint_zh: '拿着物体水平移动' },
    { id: 'place', order: 6, color: '#A78BFA', label_zh: '放置', hint_zh: '朝放置位下降' },
    { id: 'release', order: 7, color: '#F472B6', label_zh: '释放', hint_zh: '松手，物体脱离' },
  ],
  episode_fields: [],
  validation: { gap_warn_frames: 10 },
  timeline: {
    snap_adjacent_frames: 1,
    resize_handle_px: 6,
    enable_move: true,
    enable_resize: true,
    create_on_empty_track_only: true,
  },
};

function getAnnotationSchema() {
  return state.annotationSchema || DEFAULT_ANNOTATION_SCHEMA;
}

function getSubtaskLabels() {
  return [...(getAnnotationSchema().subtask_labels || [])].sort((a, b) => a.order - b.order);
}

function getLabelMap() {
  return Object.fromEntries(getSubtaskLabels().map((l) => [l.id, l]));
}

function getGapWarnFrames() {
  return getAnnotationSchema().validation?.gap_warn_frames ?? 10;
}

function getTimelineUX() {
  const timeline = getAnnotationSchema().timeline || {};
  return {
    snap_adjacent_frames: timeline.snap_adjacent_frames ?? 1,
    resize_handle_px: timeline.resize_handle_px ?? 6,
    enable_move: timeline.enable_move !== false,
    enable_resize: timeline.enable_resize !== false,
    create_on_empty_track_only: timeline.create_on_empty_track_only !== false,
  };
}

function schemaDisplayLocale() {
  const schemaLocale = getAnnotationSchema().display_locale;
  if (schemaLocale === 'en' || schemaLocale === 'zh-Hans') return schemaLocale;
  return laI18n.getLocale?.() === 'zh-Hans' ? 'zh-Hans' : 'en';
}

function schemaLabelText(lbl) {
  if (!lbl) return '';
  const useZh = schemaDisplayLocale() === 'zh-Hans';
  return (useZh ? lbl.label_zh : lbl.label_en) || lbl.label_en || lbl.label_zh || lbl.id;
}

function schemaHintText(lbl) {
  if (!lbl) return '';
  const useZh = schemaDisplayLocale() === 'zh-Hans';
  return (useZh ? lbl.hint_zh : lbl.hint_en) || lbl.hint_en || lbl.hint_zh || lbl.hint || '';
}

function applyAnnotationSchema(schema) {
  state.annotationSchema = schema || null;
  const labels = getSubtaskLabels();
  if (!labels.some((l) => l.id === state.selectedLabel)) {
    state.selectedLabel = labels[0]?.id || 'idle';
  }
  renderLabelPalette();
  renderEpisodeFields();
  updateLabelHintForSelection(state.selectedLabel);
}

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
const videoKeyToolbar = document.getElementById('videoKeyToolbar');
const videoKeyToolbarLabel = document.getElementById('videoKeyToolbarLabel');
const videoKeyToolbarButtons = document.getElementById('videoKeyToolbarButtons');
const connectHelper = document.getElementById('connectHelper');
const repoLabel = document.getElementById('repoLabel');
const localLabel = document.getElementById('localLabel');
const collectionLabel = document.getElementById('collectionLabel');
const packageLabel = document.getElementById('packageLabel');
const collectionSelect = document.getElementById('collectionSelect');
const packageSelect = document.getElementById('packageSelect');
const packageMeta = document.getElementById('packageMeta');
const toggleAdvancedPath = document.getElementById('toggleAdvancedPath');

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
const episodeFieldsGroup = document.getElementById('episodeFieldsGroup');
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
  selectedLabel: DEFAULT_ANNOTATION_SCHEMA.subtask_labels[0].id,
  annotationSchema: null,
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
  catalog: {
    loaded: false,
    collections: [],
    byId: new Map(),
    selectedCollectionId: null,
    selectedPackageId: null,
    advancedPath: false,
  },
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
  const collection = params.get('collection');
  const packageId = params.get('package');
  const episodeIndex = Number(params.get('episodeIndex') || '0');
  return {
    ego,
    datasetPath,
    collection,
    packageId,
    episodeIndex: Number.isFinite(episodeIndex) ? episodeIndex : 0,
  };
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
    state.annotations[epIdx] = { subtasks: [], high_levels: [], outcome: null, fields: {} };
  }
  return state.annotations[epIdx];
}

function getAnnotationsSnapshot(epIdx) {
  const ann = getEpisodeAnnotations(epIdx);
  const sorted = [...ann.subtasks].sort((a, b) => a.start - b.start);
  return JSON.stringify({ subtasks: sorted, outcome: ann.outcome ?? null, fields: ann.fields ?? {} });
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

function fieldLabelText(spec) {
  const zh = laI18n.getLocale?.() === 'zh-Hans';
  return (zh ? spec.label_zh : spec.label_en) || spec.label_zh || spec.label_en || spec.id;
}

function fieldHintText(spec) {
  const zh = laI18n.getLocale?.() === 'zh-Hans';
  return (zh ? spec.hint_zh : spec.hint_en) || spec.hint_zh || spec.hint_en || '';
}

function readEpisodeFieldsFromUI() {
  if (!episodeFieldsGroup || state.currentEpisode == null) return {};
  const ann = getEpisodeAnnotations(state.currentEpisode);
  const specs = getAnnotationSchema().episode_fields || [];
  const fields = {};
  specs.forEach((spec) => {
    const el = episodeFieldsGroup.querySelector(`[data-field-id="${spec.id}"]`);
    if (!el) return;
    if (spec.type === 'int') {
      const raw = el.value.trim();
      if (raw !== '') fields[spec.id] = Number.parseInt(raw, 10);
    } else if (spec.type === 'float') {
      const raw = el.value.trim();
      if (raw !== '') fields[spec.id] = Number.parseFloat(raw);
    } else if (spec.type === 'bool') {
      fields[spec.id] = el.checked;
    } else {
      const raw = el.value.trim();
      if (raw !== '') fields[spec.id] = raw;
    }
  });
  ann.fields = fields;
  return fields;
}

function setEpisodeFieldsUI(fields) {
  if (!episodeFieldsGroup) return;
  const specs = getAnnotationSchema().episode_fields || [];
  specs.forEach((spec) => {
    const el = episodeFieldsGroup.querySelector(`[data-field-id="${spec.id}"]`);
    if (!el) return;
    const value = fields?.[spec.id];
    if (spec.type === 'bool') {
      el.checked = Boolean(value);
    } else {
      el.value = value == null ? '' : String(value);
    }
  });
}

function renderEpisodeFields() {
  if (!episodeFieldsGroup) return;
  episodeFieldsGroup.innerHTML = '';
  const specs = getAnnotationSchema().episode_fields || [];
  if (!specs.length) {
    episodeFieldsGroup.hidden = true;
    return;
  }
  episodeFieldsGroup.hidden = false;
  specs.forEach((spec) => {
    const label = document.createElement('label');
    label.className = 'ls-episode-field';
    const title = document.createElement('span');
    title.className = 'ls-episode-field-label';
    title.textContent = fieldLabelText(spec);
    label.appendChild(title);

    let input;
    if (spec.type === 'text') {
      input = document.createElement('input');
      input.type = 'text';
      if (spec.max_length) input.maxLength = spec.max_length;
    } else if (spec.type === 'int' || spec.type === 'float') {
      input = document.createElement('input');
      input.type = 'number';
      if (spec.min != null) input.min = String(spec.min);
      if (spec.max != null) input.max = String(spec.max);
      if (spec.type === 'int') input.step = '1';
    } else if (spec.type === 'bool') {
      input = document.createElement('input');
      input.type = 'checkbox';
    } else {
      input = document.createElement('input');
      input.type = 'text';
    }
    input.dataset.fieldId = spec.id;
    const hint = fieldHintText(spec);
    if (hint) {
      input.title = hint;
      label.title = hint;
    }
    if (spec.required) input.required = true;
    input.addEventListener('input', () => {
      readEpisodeFieldsFromUI();
      markDirty();
    });
    input.addEventListener('change', () => {
      readEpisodeFieldsFromUI();
      markDirty();
    });
    label.appendChild(input);
    episodeFieldsGroup.appendChild(label);
  });
  if (state.currentEpisode != null) {
    setEpisodeFieldsUI(getEpisodeAnnotations(state.currentEpisode).fields);
  }
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
    if (gap > getGapWarnFrames()) {
      warnings.push({
        code: 'V-04',
        message: `帧 ${sorted[i].endFrame + 1}–${sorted[i + 1].startFrame - 1} 连续空隙 ${gap} 帧（超过 ${getGapWarnFrames()} 帧）`,
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
  return wouldOverlapSegment(startFrame, endFrame, subtasks, null);
}

function wouldOverlapSegment(startFrame, endFrame, subtasks, excludeIdx = null) {
  const candidate = { startFrame, endFrame };
  return subtasks.some((seg, idx) => {
    if (idx === excludeIdx) return false;
    return segmentsOverlap(candidate, segToFrames(seg));
  });
}

function snapFrame(frame, candidates, snapRadius) {
  let snapped = frame;
  let bestDist = snapRadius + 1;
  candidates.forEach((candidate) => {
    const dist = Math.abs(frame - candidate);
    if (dist <= snapRadius && dist < bestDist) {
      bestDist = dist;
      snapped = candidate;
    }
  });
  return snapped;
}

function getSnapCandidatesForEdge(segIdx, edge) {
  const maxFrame = Math.max(0, getTotalFrames() - 1);
  const ann = getEpisodeAnnotations(state.currentEpisode);
  const candidates = new Set([0, maxFrame]);
  ann.subtasks.forEach((seg, idx) => {
    if (idx === segIdx) return;
    const { startFrame, endFrame } = segToFrames(seg);
    if (edge === 'start') {
      candidates.add(endFrame + 1);
      candidates.add(startFrame);
    } else {
      candidates.add(startFrame - 1);
      candidates.add(endFrame);
    }
  });
  return [...candidates].filter((f) => f >= 0 && f <= maxFrame);
}

function clampSegmentMove(origStart, origEnd, delta, maxFrame) {
  let start = origStart + delta;
  let end = origEnd + delta;
  if (start < 0) {
    end -= start;
    start = 0;
  }
  if (end > maxFrame) {
    start -= end - maxFrame;
    end = maxFrame;
  }
  return { start: Math.max(0, start), end: Math.min(maxFrame, end) };
}

function regionBarHitMode(bar, clientX) {
  const ux = getTimelineUX();
  const rect = bar.getBoundingClientRect();
  const x = clientX - rect.left;
  if (ux.enable_resize && x <= ux.resize_handle_px) return 'resize-start';
  if (ux.enable_resize && x >= rect.width - ux.resize_handle_px) return 'resize-end';
  if (ux.enable_move) return 'move';
  return 'select';
}

function segmentBarTitle(seg, startFrame, endFrame) {
  const lbl = getLabelMap()[seg.label];
  const name = schemaLabelText(lbl) || seg.label;
  const hint = schemaHintText(lbl);
  return hint
    ? `${name}（${hint}）: f${startFrame}–f${endFrame}`
    : `${name}: f${startFrame}–f${endFrame}`;
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
  return Boolean(getLabelMap()[label]);
}

function updateLabelHintForSelection(labelId) {
  if (!labelHint) return;
  const lbl = getLabelMap()[labelId];
  labelHint.textContent = lbl
    ? t('label_hint_selected', { id: schemaLabelText(lbl), hint: schemaHintText(lbl) })
    : t('label_hint_default');
  labelHint.classList.remove('error');
}

function renderLabelPalette() {
  if (!labelPalette) return;
  labelPalette.innerHTML = '';
  const labels = getSubtaskLabels();
  if (labelCount) labelCount.textContent = t('label_count', { n: labels.length });

  labels.forEach((lbl) => {
    const tag = document.createElement('span');
    tag.className = 'ls-label-tag';
    tag.textContent = schemaLabelText(lbl);
    tag.dataset.label = lbl.id;
    tag.style.backgroundColor = lbl.color;
    tag.title = schemaHintText(lbl);
    tag.setAttribute('role', 'option');
    tag.setAttribute('aria-label', `${schemaLabelText(lbl)}: ${schemaHintText(lbl)}`);
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
    bar.dataset.segIdx = String(idx);
    const color = getLabelMap()[seg.label]?.color || '#576cc9';
    bar.style.setProperty('--region-color', color);
    bar.style.left = `${frameToPercent(startFrame)}%`;
    bar.style.width = `${Math.max(frameToPercent(endFrame + 1) - frameToPercent(startFrame), 0.5)}%`;
    bar.title = segmentBarTitle(seg, startFrame, endFrame);
    bar.setAttribute('aria-label', bar.title);
    if (!isStandardLabel(seg.label)) bar.classList.add('nonstandard');
    if (state.selectedRegionIdx === idx) bar.classList.add('selected');
    if (state.conflictIndices.has(idx)) bar.classList.add('conflict');

    const handleStart = document.createElement('span');
    handleStart.className = 'ls-region-handle ls-region-handle-start';
    handleStart.title = t('timeline_resize_start');

    const body = document.createElement('span');
    body.className = 'ls-region-body';

    const handleEnd = document.createElement('span');
    handleEnd.className = 'ls-region-handle ls-region-handle-end';
    handleEnd.title = t('timeline_resize_end');

    bar.appendChild(handleStart);
    bar.appendChild(body);
    bar.appendChild(handleEnd);

    bar.addEventListener('mousedown', (e) => onRegionBarMouseDown(e, idx));
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
    swatch.style.background = getLabelMap()[seg.label]?.color || '#576cc9';

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
    labelSpan.textContent = schemaLabelText(getLabelMap()[seg.label]) || seg.label;
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
  const dragColor = getLabelMap()[state.selectedLabel]?.color || '#576cc9';
  dragPreview.hidden = false;
  dragPreview.style.left = `${left}%`;
  dragPreview.style.width = `${width}%`;
  dragPreview.style.setProperty('--drag-color', dragColor);
}

function hideDragPreview() {
  dragPreview.hidden = true;
}

function onRegionBarMouseDown(e, segIdx) {
  if (state.currentEpisode == null || e.button !== 0) return;
  e.stopPropagation();
  const bar = e.currentTarget;
  let mode = 'select';
  if (e.target.classList.contains('ls-region-handle-start')) mode = 'resize-start';
  else if (e.target.classList.contains('ls-region-handle-end')) mode = 'resize-end';
  else mode = regionBarHitMode(bar, e.clientX);

  state.selectedRegionIdx = segIdx;
  renderRegionList();

  const ux = getTimelineUX();
  if (mode === 'resize-start' && !ux.enable_resize) mode = 'select';
  if (mode === 'resize-end' && !ux.enable_resize) mode = 'select';
  if (mode === 'move' && !ux.enable_move) mode = 'select';

  if (mode === 'select') {
    renderTimelineRegions();
    return;
  }

  const ann = getEpisodeAnnotations(state.currentEpisode);
  const { startFrame, endFrame } = segToFrames(ann.subtasks[segIdx]);
  state.drag = {
    mode,
    segIdx,
    pointerStartFrame: percentToFrame(trackXToPercent(e.clientX)),
    origStart: startFrame,
    origEnd: endFrame,
    moved: false,
  };
  document.body.classList.add('ls-timeline-dragging');
  e.preventDefault();
}

function applySegmentEditDrag(clientX) {
  const drag = state.drag;
  if (!drag || drag.mode === 'create') return false;
  if (!['move', 'resize-start', 'resize-end'].includes(drag.mode)) return false;

  const ux = getTimelineUX();
  const maxFrame = Math.max(0, getTotalFrames() - 1);
  const frame = percentToFrame(trackXToPercent(clientX));
  const ann = getEpisodeAnnotations(state.currentEpisode);
  const seg = ann.subtasks[drag.segIdx];
  let startFrame = drag.origStart;
  let endFrame = drag.origEnd;

  if (drag.mode === 'resize-start') {
    startFrame = snapFrame(
      frame,
      getSnapCandidatesForEdge(drag.segIdx, 'start'),
      ux.snap_adjacent_frames,
    );
    startFrame = Math.min(startFrame, endFrame);
  } else if (drag.mode === 'resize-end') {
    endFrame = snapFrame(
      frame,
      getSnapCandidatesForEdge(drag.segIdx, 'end'),
      ux.snap_adjacent_frames,
    );
    endFrame = Math.max(endFrame, startFrame);
  } else if (drag.mode === 'move') {
    const delta = frame - drag.pointerStartFrame;
    const clamped = clampSegmentMove(drag.origStart, drag.origEnd, delta, maxFrame);
    startFrame = clamped.start;
    endFrame = clamped.end;
  }

  if (startFrame > endFrame) return false;
  if (wouldOverlapSegment(startFrame, endFrame, ann.subtasks, drag.segIdx)) return false;

  Object.assign(seg, framesToSeg(startFrame, endFrame, seg.label));
  drag.moved = true;
  return true;
}

function onTimelineMouseDown(e) {
  if (state.currentEpisode == null || e.button !== 0) return;
  if (e.target !== timelineTrack && e.target !== timelineRegions) return;
  if (!state.selectedLabel || !isStandardLabel(state.selectedLabel)) {
    labelHint.textContent = t('label_hint_pick');
    labelHint.classList.add('error');
    return;
  }
  const pct = trackXToPercent(e.clientX);
  state.drag = {
    mode: 'create',
    startPct: pct,
    currentPct: pct,
    moved: false,
  };
  showDragPreview(pct, pct);
  document.body.classList.add('ls-timeline-dragging');
  e.preventDefault();
}

function onTimelineMouseMove(e) {
  if (!state.drag) return;
  if (state.drag.mode === 'create') {
    const pct = trackXToPercent(e.clientX);
    if (Math.abs(pct - state.drag.startPct) > 0.005) {
      state.drag.moved = true;
    }
    state.drag.currentPct = pct;
    showDragPreview(state.drag.startPct, pct);
    return;
  }
  if (applySegmentEditDrag(e.clientX)) {
    renderAllTimeline();
  }
}

function onTimelineMouseUp(e) {
  if (!state.drag) return;
  const drag = state.drag;
  state.drag = null;
  document.body.classList.remove('ls-timeline-dragging');
  hideDragPreview();

  if (drag.mode === 'create') {
    const { startPct, currentPct, moved } = drag;
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
    return;
  }

  if (drag.moved) {
    markDirty();
    renderAllTimeline();
  }
}

function bindTimelineEvents() {
  timelineTrack.addEventListener('mousedown', onTimelineMouseDown);
  timelineRegions.addEventListener('mousedown', onTimelineMouseDown);
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
      fields: {},
    };
    } catch (err) {
      setHelper(connectHelper, err.message);
      state.annotations[epIdx] = { subtasks: [], high_levels: [], outcome: null, fields: {} };
    }
  } else {
    const res = await fetch(`/api/episodes/${epIdx}/annotations`);
    const data = await res.json();
    state.annotations[epIdx] = {
      subtasks: data.subtasks || [],
      high_levels: data.high_levels || [],
      outcome: data.outcome ?? null,
      fields: data.fields || {},
    };
  }
  setOutcomeUI(state.annotations[epIdx].outcome);
  setEpisodeFieldsUI(state.annotations[epIdx].fields);

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

  readEpisodeFieldsFromUI();
  const payload = {
    episode_index: state.currentEpisode,
    subtasks: ann.subtasks,
    high_levels: ann.high_levels,
    fields: ann.fields || {},
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
// Video camera toolbar (above player)
// ---------------------------------------------------------------------------

function formatVideoKeyLabel(key) {
  const short = (key || '').split('.').pop() || key;
  const map = {
    head_camera: t('camera_head'),
    camera1: t('camera_1'),
    camera2: t('camera_2'),
    image: t('camera_main'),
  };
  return map[short] || short;
}

function renderVideoKeyToolbar(keys, selected) {
  if (!videoKeyToolbar || !videoKeyToolbarButtons) return;
  const list = Array.isArray(keys) ? keys : [];
  if (list.length <= 1) {
    videoKeyToolbar.hidden = true;
    videoKeyToolbarButtons.innerHTML = '';
    return;
  }
  videoKeyToolbar.hidden = false;
  if (videoKeyToolbarLabel) {
    videoKeyToolbarLabel.textContent = t('label_camera_view');
  }
  videoKeyToolbarButtons.innerHTML = '';
  list.forEach((key) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `ls-video-view-btn${key === selected ? ' is-active' : ''}`;
    btn.textContent = formatVideoKeyLabel(key);
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', key === selected ? 'true' : 'false');
    btn.title = key;
    btn.addEventListener('click', () => switchVideoKey(key));
    videoKeyToolbarButtons.appendChild(btn);
  });
}

async function switchVideoKey(newKey) {
  if (!state.dataset || !newKey || newKey === state.dataset.selected_video_key) return;
  state.dataset.selected_video_key = newKey;
  renderVideoKeyToolbar(state.dataset.video_keys, newKey);
  if (state.currentEpisode == null) return;
  const epIdx = state.currentEpisode;
  episodeVideo.src = `/api/video/${epIdx}?video_key=${encodeURIComponent(newKey)}`;
}

// ---------------------------------------------------------------------------
// Dataset catalog (Collection → Package)
// ---------------------------------------------------------------------------

function formatPackageOption(pkg) {
  const parts = [pkg.display_name || pkg.id];
  if (pkg.episodes != null) parts.push(`${pkg.episodes} ep`);
  return parts.join(' · ');
}

function formatCollectionOption(col) {
  const title = col.title || col.id;
  const count = col.package_count ?? 0;
  return t('catalog_collection_option', { title, count });
}

function updatePackageMeta(pkg) {
  if (!packageMeta) return;
  if (!pkg) {
    packageMeta.hidden = true;
    packageMeta.textContent = '';
    return;
  }
  const bits = [];
  if (pkg.robot_type) bits.push(pkg.robot_type);
  if (pkg.episodes != null) bits.push(`${pkg.episodes} episodes`);
  if (pkg.fps != null) bits.push(`${pkg.fps} fps`);
  if (Array.isArray(pkg.video_keys) && pkg.video_keys.length) bits.push(pkg.video_keys.join(', '));
  if (pkg.task_preview) bits.push(pkg.task_preview);
  packageMeta.textContent = bits.join(' | ');
  packageMeta.hidden = bits.length === 0;
}

function setAdvancedPathMode(enabled) {
  state.catalog.advancedPath = enabled;
  if (!localInput) return;
  localInput.readOnly = !enabled && state.catalog.selectedPackageId != null;
  if (toggleAdvancedPath) {
    toggleAdvancedPath.hidden = sourceSelect?.value !== 'local' || !state.catalog.loaded;
    toggleAdvancedPath.textContent = enabled ? t('catalog_hide_advanced') : t('catalog_advanced_path');
  }
}

function applyPackageSelection(pkg) {
  if (!pkg || !localInput) return;
  state.catalog.selectedPackageId = pkg.id;
  localInput.value = pkg.local_path || '';
  updatePackageMeta(pkg);
  setAdvancedPathMode(state.catalog.advancedPath);
}

function populatePackageSelect(collectionId, preferredPackageId) {
  if (!packageSelect) return;
  packageSelect.innerHTML = '';
  const collection = state.catalog.byId.get(collectionId);
  const packages = collection?.packages || [];
  packages.forEach((pkg) => {
    const option = document.createElement('option');
    option.value = pkg.id;
    option.textContent = formatPackageOption(pkg);
    if (pkg.id === preferredPackageId) option.selected = true;
    packageSelect.appendChild(option);
  });
  const selected = packages.find((p) => p.id === preferredPackageId) || packages[0];
  if (selected) applyPackageSelection(selected);
  else updatePackageMeta(null);
}

function populateCollectionSelect(preferredCollectionId, preferredPackageId) {
  if (!collectionSelect) return;
  collectionSelect.innerHTML = '';
  state.catalog.collections.forEach((col) => {
    const option = document.createElement('option');
    option.value = col.id;
    option.textContent = formatCollectionOption(col);
    if (col.id === preferredCollectionId) option.selected = true;
    collectionSelect.appendChild(option);
  });
  const selectedId = preferredCollectionId || state.catalog.collections[0]?.id;
  state.catalog.selectedCollectionId = selectedId || null;
  if (selectedId) populatePackageSelect(selectedId, preferredPackageId);
}

async function fetchCatalog() {
  try {
    const res = await fetch('/api/datasets/collections');
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to load catalog');
    state.catalog.collections = data.collections || [];
    state.catalog.byId = new Map();
    for (const summary of state.catalog.collections) {
      const detailRes = await fetch(`/api/datasets/collections/${encodeURIComponent(summary.id)}`);
      const detail = await detailRes.json();
      if (detailRes.ok) state.catalog.byId.set(summary.id, detail);
    }
    state.catalog.loaded = state.catalog.collections.length > 0;
  } catch (err) {
    console.warn('catalog fetch failed', err);
    state.catalog.loaded = false;
  }
}

function pickDefaultCatalogSelection() {
  const params = parseEgoUrlParams();
  if (params.collection && state.catalog.byId.has(params.collection)) {
    return {
      collectionId: params.collection,
      packageId: params.packageId || state.catalog.byId.get(params.collection)?.packages?.[0]?.id,
    };
  }
  if (state.catalog.byId.has('limx_box_transport')) {
    const pkgs = state.catalog.byId.get('limx_box_transport')?.packages || [];
    return { collectionId: 'limx_box_transport', packageId: pkgs[0]?.id };
  }
  if (state.catalog.byId.has('pusht')) {
    return { collectionId: 'pusht', packageId: 'pusht' };
  }
  const first = state.catalog.collections[0];
  const firstPkg = state.catalog.byId.get(first?.id)?.packages?.[0];
  return { collectionId: first?.id, packageId: firstPkg?.id };
}

async function initCatalogUi() {
  await fetchCatalog();
  const defaults = pickDefaultCatalogSelection();
  populateCollectionSelect(defaults.collectionId, defaults.packageId);
  const params = parseEgoUrlParams();
  if (params.datasetPath && localInput) {
    localInput.value = params.datasetPath;
    state.catalog.advancedPath = true;
    setAdvancedPathMode(true);
  }
  updateConnectFields();
}

// ---------------------------------------------------------------------------
// Connect / load
// ---------------------------------------------------------------------------

function updateConnectFields() {
  const isLocal = sourceSelect.value === 'local';
  const showCatalog = isLocal && state.catalog.loaded;
  if (repoLabel) repoLabel.style.display = isLocal ? 'none' : 'flex';
  if (localLabel) localLabel.style.display = isLocal ? 'flex' : 'none';
  if (collectionLabel) collectionLabel.style.display = showCatalog ? 'flex' : 'none';
  if (packageLabel) packageLabel.style.display = showCatalog ? 'flex' : 'none';
  if (packageMeta) packageMeta.hidden = !showCatalog || !packageMeta.textContent;
  const revisionLabel = document.getElementById('revisionLabel');
  if (revisionLabel) revisionLabel.style.display = isLocal ? 'none' : 'flex';
  if (isLocal && localInput && !localInput.value.trim() && !state.catalog.loaded) {
    localInput.value = DEMO_LOCAL_DATASET;
  }
  setAdvancedPathMode(state.catalog.advancedPath);
  setHelper(
    connectHelper,
    isLocal
      ? (showCatalog ? t('connect_helper_catalog') : t('connect_helper_local'))
      : t('connect_helper_hf'),
  );
}

function populateVideoKeys(keys, selected) {
  renderVideoKeyToolbar(keys, selected);
}

sourceSelect.addEventListener('change', updateConnectFields);

if (collectionSelect) {
  collectionSelect.addEventListener('change', () => {
    state.catalog.selectedCollectionId = collectionSelect.value;
    populatePackageSelect(collectionSelect.value, null);
    updateConnectFields();
  });
}

if (packageSelect) {
  packageSelect.addEventListener('change', () => {
    const collection = state.catalog.byId.get(state.catalog.selectedCollectionId);
    const pkg = (collection?.packages || []).find((p) => p.id === packageSelect.value);
    if (pkg) applyPackageSelection(pkg);
  });
}

if (toggleAdvancedPath) {
  toggleAdvancedPath.addEventListener('click', () => {
    setAdvancedPathMode(!state.catalog.advancedPath);
  });
}

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
    video_key: null,
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
    applyAnnotationSchema(data.annotation_schema);
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
  state.annotations[state.currentEpisode] = { subtasks: [], high_levels: [], outcome: null, fields: {} };
  setEpisodeFieldsUI({});
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
  if (state.dataset?.video_keys) {
    renderVideoKeyToolbar(state.dataset.video_keys, state.dataset.selected_video_key);
  }
  if (state.annotationSchema) {
    renderLabelPalette();
    renderEpisodeFields();
    updateLabelHintForSelection(state.selectedLabel);
  }
  updateLabelHintForSelection(state.selectedLabel);
  updateSaveUI();
};

window.__laOnLocaleChange();

workspace.style.display = 'none';
renderLabelPalette();
updateLabelHintForSelection(state.selectedLabel);
bindTimelineEvents();
updateSaveUI();

initCatalogUi().then(() => {
  const egoParams = parseEgoUrlParams();
  if (egoParams.datasetPath && localInput) {
    localInput.value = egoParams.datasetPath;
    if (sourceSelect) sourceSelect.value = 'local';
    state.catalog.advancedPath = true;
    setAdvancedPathMode(true);
  } else if (sourceSelect) {
    sourceSelect.value = 'local';
  }
  updateConnectFields();
});
