/* LeRobot Annotate UI locale (Data Lab embed + standalone). */
(function () {
  const EMBED_PARAMS = new URLSearchParams(window.location.search);
  const IS_DATALAB_EMBED = EMBED_PARAMS.get('datalab_embed') === '1';
  const UI_LOCALE_STORAGE_KEY = 'ui_locale';

  const MESSAGES = {
    en: {
      brand_mark: 'LS',
      brand_title: 'LeRobot Annotate',
      brand_sub: 'Video frame classification',
      embed_brand_mark: 'DL',
      embed_brand_title: 'Embodied Annotate',
      embed_brand_sub: 'Video frame classification',
      status_disconnected: 'Disconnected',
      status_loaded: 'Loaded {name}',
      status_saved: 'Saved ✓',
      connect_toggle: 'Connect dataset',
      label_source: 'Source',
      source_local: 'Local directory',
      source_hf: 'Hugging Face Hub',
      label_repo: 'Repo ID',
      label_local_path: 'Dataset directory',
      label_revision: 'Revision',
      label_video_key: 'Video key',
      btn_load: 'Load',
      connect_helper_local: 'Local: enter the dataset directory below, then Load.',
      connect_helper_hf: 'Hub: enter repo id, e.g. lerobot/pusht',
      connect_local_required: 'Dataset directory is required.',
      connect_loading: 'Loading dataset...',
      connect_loaded: 'Loaded {count} episodes.',
      connect_episode_saved: 'Episode {idx} saved.',
      episodes_title: 'Episodes',
      episode_search: 'Filter #',
      episode_select: 'Select an episode',
      episode_title: 'Episode {idx}',
      btn_clear: 'Clear',
      task_label: 'Task',
      timeline_labels: 'Timeline labels',
      label_count: 'Labels ({n})',
      label_hint_default: 'Select a label, then drag on the timeline; segments should not overlap.',
      label_hint_selected: '{id}: {hint}',
      label_hint_pick: 'Select one of the 8 fixed labels first',
      label_hint_overlap: 'Overlaps an existing segment — adjust start/end frames',
      regions_title: 'Regions',
      outcome_label: 'Outcome',
      save_button: 'Save segment annotations',
      save_dirty: 'Unsaved',
      save_ok: 'Saved ✓',
      validation_cannot_save: 'Cannot save',
      validation_warnings: 'Warnings',
      validation_back: 'Back to edit',
      validation_force_save: 'Save anyway',
      non_standard_badge: 'non-standard',
      export_title: 'Export',
      export_output: 'Output directory',
      export_output_ph: 'default: data/exports',
      export_copy_videos: 'Copy videos',
      export_btn: 'Export locally',
      export_running: 'Exporting...',
      export_done: 'Exported to {dir} (subtasks: {subtasks}, high-level: {hl})',
      export_failed: 'Export failed',
      save_failed: 'Save failed',
      hl_summary: 'High-level dialogue (optional)',
      hl_start: 'Start (s)',
      hl_end: 'End (s)',
      hl_user: 'User prompt',
      hl_robot: 'Robot response',
      hl_set_start: 'Set start',
      hl_set_end: 'Set end',
      hl_add: 'Add',
      hl_delete: 'Delete',
      confirm_leave: 'Segment annotations are not saved. Leave anyway?',
      frame_readout: 'frame {cur} / {max}',
    },
    'zh-Hans': {
      brand_mark: 'LS',
      brand_title: 'LeRobot 标注',
      brand_sub: '视频帧分段标注',
      embed_brand_mark: 'DL',
      embed_brand_title: '具身标注',
      embed_brand_sub: '视频帧分段标注',
      status_disconnected: '未连接',
      status_loaded: '已加载 {name}',
      status_saved: '已保存 ✓',
      connect_toggle: '连接数据集',
      label_source: '数据源',
      source_local: '本地目录',
      source_hf: 'Hugging Face Hub',
      label_repo: '仓库 ID',
      label_local_path: '数据集目录',
      label_revision: '版本',
      label_video_key: '视频键',
      btn_load: '加载',
      connect_helper_local: '本地模式：填写下方数据集目录后点击「加载」。',
      connect_helper_hf: 'Hub：填写仓库 ID，例如 lerobot/pusht',
      connect_local_required: '请填写数据集目录。',
      connect_loading: '正在加载数据集...',
      connect_loaded: '已加载 {count} 个 episode。',
      connect_episode_saved: 'Episode {idx} 已保存。',
      episodes_title: 'Episodes',
      episode_search: '筛选 #',
      episode_select: '请选择一个 episode',
      episode_title: 'Episode {idx}',
      btn_clear: '清空',
      task_label: '任务',
      timeline_labels: '时间轴标签',
      label_count: '标签（{n}）',
      label_hint_default: '先选标签，再在时间轴拖拽；相邻分段不重叠，尽量覆盖全片',
      label_hint_selected: '{id}: {hint}',
      label_hint_pick: '请先从 8 类固定标签中选择一项',
      label_hint_overlap: '与已有分段重叠，请调整起止帧',
      regions_title: 'Regions',
      outcome_label: '结果',
      save_button: '保存当前片段标注',
      save_dirty: '未保存',
      save_ok: '已保存 ✓',
      validation_cannot_save: '无法保存',
      validation_warnings: '存在警告',
      validation_back: '返回修改',
      validation_force_save: '仍要保存',
      non_standard_badge: '非标准标签',
      export_title: '导出',
      export_output: '输出目录',
      export_output_ph: '默认：data/exports',
      export_copy_videos: '复制视频',
      export_btn: '导出到本地',
      export_running: '正在导出...',
      export_done: '已导出到 {dir}（subtasks: {subtasks}，high-level: {hl}）',
      export_failed: '导出失败',
      save_failed: '保存失败',
      hl_summary: '高层对话（可选）',
      hl_start: '开始 (s)',
      hl_end: '结束 (s)',
      hl_user: '用户指令',
      hl_robot: '机器人回复',
      hl_set_start: '设为起点',
      hl_set_end: '设为终点',
      hl_add: '添加',
      hl_delete: '删除',
      confirm_leave: '当前片段标注未保存，确定离开？',
      frame_readout: '帧 {cur} / {max}',
    },
  };

  function normalizeLocale(raw) {
    if (!raw) return 'en';
    const s = String(raw).toLowerCase();
    if (s.startsWith('zh')) return 'zh-Hans';
    return 'en';
  }

  function readLocale() {
    const fromParam = EMBED_PARAMS.get('lang');
    if (fromParam) return normalizeLocale(fromParam);
    try {
      const stored = localStorage.getItem(UI_LOCALE_STORAGE_KEY);
      if (stored) return normalizeLocale(stored);
    } catch {
      /* ignore */
    }
    return 'en';
  }

  let locale = readLocale();

  function t(key, vars) {
    const bag = MESSAGES[locale] || MESSAGES.en;
    let text = bag[key] ?? MESSAGES.en[key] ?? key;
    if (vars) {
      Object.entries(vars).forEach(([k, v]) => {
        text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v));
      });
    }
    return text;
  }

  function setLabelText(labelEl, text) {
    if (!labelEl) return;
    const control = labelEl.querySelector('input, select, textarea, button');
    labelEl.childNodes.forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) node.remove();
    });
    if (control) {
      labelEl.insertBefore(document.createTextNode(`${text}\n            `), control);
    } else {
      labelEl.textContent = text;
    }
  }

  function applyStaticUI() {
    document.documentElement.lang = locale === 'zh-Hans' ? 'zh-Hans' : 'en';

    const brandMark = document.querySelector('.ls-brand-mark');
    const brandTitle = document.querySelector('.ls-brand strong');
    const brandSub = document.querySelector('.ls-brand-sub');
    const markKey = IS_DATALAB_EMBED ? 'embed_brand_mark' : 'brand_mark';
    const titleKey = IS_DATALAB_EMBED ? 'embed_brand_title' : 'brand_title';
    const subKey = IS_DATALAB_EMBED ? 'embed_brand_sub' : 'brand_sub';
    if (brandMark) brandMark.textContent = t(markKey);
    if (brandTitle) brandTitle.textContent = t(titleKey);
    if (brandSub) brandSub.textContent = t(subKey);

    const connectToggle = document.getElementById('connectToggle');
    if (connectToggle) connectToggle.textContent = t('connect_toggle');

    setLabelText(document.getElementById('sourceLabel'), t('label_source'));
    const localOpt = document.querySelector('#sourceSelect option[value="local"]');
    if (localOpt) localOpt.textContent = t('source_local');
    const hfOpt = document.querySelector('#sourceSelect option[value="hf"]');
    if (hfOpt) hfOpt.textContent = t('source_hf');

    setLabelText(document.getElementById('repoLabel'), t('label_repo'));
    setLabelText(document.getElementById('localLabel'), t('label_local_path'));
    setLabelText(document.getElementById('revisionLabel'), t('label_revision'));
    setLabelText(document.getElementById('videoKeyLabel'), t('label_video_key'));

    const loadBtn = document.querySelector('#connectForm button[type="submit"]');
    if (loadBtn) loadBtn.textContent = t('btn_load');

    const epTitle = document.querySelector('.ls-sidebar-head h2');
    if (epTitle) epTitle.textContent = t('episodes_title');
    const epSearch = document.getElementById('episodeSearch');
    if (epSearch) epSearch.placeholder = t('episode_search');
    const episodeTitleEl = document.getElementById('episodeTitle');
    if (episodeTitleEl && episodeTitleEl.textContent === 'Select an episode') {
      episodeTitleEl.textContent = t('episode_select');
    }
    const resetBtn = document.getElementById('resetEpisode');
    if (resetBtn) resetBtn.textContent = t('btn_clear');

    const taskLabel = document.querySelector('.ls-task-label');
    if (taskLabel) taskLabel.textContent = t('task_label');
    const labelsTitle = document.querySelector('.ls-labels-panel-title');
    if (labelsTitle) labelsTitle.textContent = t('timeline_labels');

    const regionsHead = document.querySelector('.ls-region-list-head span');
    if (regionsHead) regionsHead.textContent = t('regions_title');
    const outcomeLabel = document.querySelector('.ls-outcome-label');
    if (outcomeLabel) outcomeLabel.textContent = t('outcome_label');

    const exportTitle = document.querySelector('#exportPanel > h2');
    if (exportTitle) exportTitle.textContent = t('export_title');
    setLabelText(document.querySelector('#exportPanel label.ls-grow'), t('export_output'));
    const outputDir = document.getElementById('outputDir');
    if (outputDir) outputDir.placeholder = t('export_output_ph');
    const copyVideosLabel = document.querySelector('#exportPanel label.ls-check');
    if (copyVideosLabel) {
      const cb = copyVideosLabel.querySelector('input');
      copyVideosLabel.childNodes.forEach((n) => {
        if (n.nodeType === Node.TEXT_NODE) n.remove();
      });
      copyVideosLabel.appendChild(document.createTextNode(` ${t('export_copy_videos')}`));
      if (cb) copyVideosLabel.insertBefore(cb, copyVideosLabel.firstChild);
    }
    const exportBtn = document.getElementById('exportBtn');
    if (exportBtn) exportBtn.textContent = t('export_btn');

    const hlDetails = document.querySelector('.ls-editor details.ls-advanced summary');
    if (hlDetails) hlDetails.textContent = t('hl_summary');

    const statusEl = document.getElementById('status');
    if (statusEl && statusEl.textContent === 'Disconnected') {
      statusEl.textContent = t('status_disconnected');
    }
  }

  function applyDatalabEmbedMode() {
    if (!IS_DATALAB_EMBED) return;

    const sourceSelect = document.getElementById('sourceSelect');
    if (sourceSelect) {
      const hfOpt = sourceSelect.querySelector('option[value="hf"]');
      if (hfOpt) hfOpt.remove();
      sourceSelect.value = 'local';
    }

    const pushHubPanel = document.getElementById('pushHubPanel');
    if (pushHubPanel) pushHubPanel.hidden = true;
  }

  function setLocale(next) {
    locale = normalizeLocale(next);
    applyStaticUI();
    if (typeof window.__laOnLocaleChange === 'function') {
      window.__laOnLocaleChange(locale);
    }
  }

  window.addEventListener('storage', (event) => {
    if (!IS_DATALAB_EMBED || event.key !== UI_LOCALE_STORAGE_KEY) return;
    setLocale(event.newValue || 'en');
  });

  window.LA_I18N = {
    IS_DATALAB_EMBED,
    t,
    getLocale: () => locale,
    setLocale,
    applyStaticUI,
    applyDatalabEmbedMode,
    init() {
      applyDatalabEmbedMode();
      applyStaticUI();
    },
  };
})();
