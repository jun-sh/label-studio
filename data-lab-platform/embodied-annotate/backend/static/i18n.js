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
      label_collection: 'Collection',
      label_package: 'Package',
      label_job: 'Annotation job',
      label_revision: 'Revision',
      label_camera_view: 'Camera view',
      video_source_pane_aria: 'Switch camera: {name}',
      camera_head: 'Head cam',
      camera_1: 'Camera 1',
      camera_2: 'Camera 2',
      camera_main: 'Main view',
      btn_load: 'Load',
      connect_helper_local: 'Local: enter the dataset directory below, then Load.',
      connect_helper_catalog: 'Select a collection and package, then Load.',
      connect_helper_job: 'Job: {name} · {count} episodes. Click Load.',
      connect_helper_job_pick: 'Select an open annotation job for this package, then Load.',
      connect_requires_job: 'This package has multiple jobs. Select an open job above. Available: {jobs}',
      connect_job_paused: 'The selected job is paused and cannot be loaded.',
      connect_helper_hf: 'Hub: enter repo id, e.g. lerobot/pusht',
      job_option: '{name} · {family} · {count} ep',
      job_option_paused: ' (paused)',
      job_meta: '{family} · {schema} · {scope} · {status}',
      job_banner_sub: '{family} · {schema} · {count} episodes',
      job_scope_full: 'full',
      job_scope_sample: 'sample',
      job_status_open: 'open',
      job_status_paused: 'paused',
      catalog_advanced_path: 'Advanced path',
      catalog_hide_advanced: 'Use catalog path',
      catalog_collection_option: '{title} · {count} subsets',
      connect_local_required: 'Dataset directory is required.',
      connect_loading: 'Loading dataset...',
      connect_loaded: 'Loaded {count} episodes.',
      connect_episode_saved: 'Episode {idx} saved.',
      episodes_title: 'Episodes',
      episode_search: 'Filter #',
      episode_filter_all: 'All',
      episode_filter_complete: 'Complete',
      episode_filter_partial: 'In progress',
      episode_filter_none: 'Not started',
      episode_progress_summary: '{complete} / {total} complete · {partial} in progress',
      episode_archive_summary: 'Advanced (optional)',
      episode_archive_hint: 'Box count auto-summed; skill review for leads. Annotators can skip this section.',
      advanced_hl_subsection: 'High-level dialogue',
      episode_field_auto_suffix: '(auto)',
      episode_status_complete: 'Done',
      episode_status_partial: 'Draft',
      episode_status_none: 'New',
      soft_warn_W_EP_01: 'Outcome is fail but box_cycle > 0',
      soft_warn_W_EP_02: 'Outcome is success but box_cycle is 0',
      soft_warn_W_EP_03: 'Outcome is fail but a box is marked successful',
      soft_warn_W_EP_04: 'Outcome is success but a box is marked failed',
      soft_warn_W_EP_05: 'box_cycle does not match per-box success count',
      soft_warn_W_EP_06: 'Outcome is not selected',
      soft_warn_W_EP_07: 'Some boxes have unset success',
      episode_select: 'Select an episode',
      episode_title: 'Episode {idx}',
      episode_meta: '{frames} frames @ {fps} fps • {duration}',
      outcome_aria_label: 'Episode outcome',
      skill_auto_badge: 'L1 · auto',
      btn_clear: 'Clear',
      task_label: 'Task',
      timeline_labels: 'Timeline labels',
      label_count: 'Labels ({n})',
      label_hint_default: 'Select a label, then drag on empty timeline to create; drag segment edges to resize or the middle to move.',
      label_hint_selected: '{id}: {hint}',
      label_hint_pick: 'Select one of the 8 fixed labels first',
      label_hint_overlap: 'Overlaps an existing segment — adjust start/end frames',
      timeline_resize_start: 'Drag to adjust start frame',
      timeline_resize_end: 'Drag to adjust end frame',
      timeline_playhead_scrub: 'Drag to scrub video frame',
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
      export_hint: 'Export training Parquet; unannotated episodes keep frame index -1.',
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
      skill_derivation_title: 'Atomic skills (live preview)',
      skill_derivation_hint: 'Edit the 8 phase labels above — L1 skills update instantly. Fix L2 boundaries if something looks wrong; saved together on Save.',
      skill_derivation_no_phases: 'Select labels and drag on the timeline to see derived L1 skills.',
      skill_derivation_incomplete: 'Add or adjust phase labels (reach → … → release) — L1 will update as you edit.',
      skill_derivation_warnings: 'Derivation notes',
      cycle_fields_title: 'Per-box results',
      cycle_fields_hint: 'Auto baseline from L2 phases; only correct exceptions and pick fail reason.',
      cycle_success_auto: 'auto',
      cycle_success_manual: 'manual override',
      cycle_row_title: 'Box {id} · {start}s–{end}s · {struct}',
      cycle_struct_complete: 'structure complete',
      cycle_struct_incomplete: 'structure incomplete',
      cycle_success_unset: 'Unset',
      cycle_success_yes: 'Success',
      cycle_success_no: 'Failed',
      enum_cycle_fail_reason_none: '—',
      enum_cycle_fail_reason_slip_grasp: 'Slip at grasp',
      enum_cycle_fail_reason_drop_mid_transport: 'Dropped mid carry',
      enum_cycle_fail_reason_place_offset: 'Place offset',
      enum_cycle_fail_reason_incomplete: 'Incomplete cycle',
      enum_cycle_fail_reason_other: 'Other',
      skill_cycle_label: 'Box {id}',
      enum_skill_review_pending: 'Pending review',
      enum_skill_review_approved: 'Approved',
      enum_skill_review_rejected: 'Needs fix',
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
      label_collection: '数据集集合',
      label_package: '子数据集',
      label_job: '标注任务',
      label_revision: '版本',
      label_camera_view: '相机视角',
      video_source_pane_aria: '切换相机：{name}',
      camera_head: '头摄',
      camera_1: '相机 1',
      camera_2: '相机 2',
      camera_main: '主视角',
      btn_load: '加载',
      connect_helper_local: '本地模式：填写下方数据集目录后点击「加载」。',
      connect_helper_catalog: '选择数据集集合与子包，然后点击「加载」。',
      connect_helper_job: '任务：{name} · {count} 条 episode。点击「加载」。',
      connect_helper_job_pick: '请为该子包选择一个进行中的标注任务，然后点击「加载」。',
      connect_requires_job: '该子包有多个标注任务，请先在上方选择进行中的任务。可选：{jobs}',
      connect_job_paused: '所选任务已暂停，无法加载。',
      connect_helper_hf: 'Hub：填写仓库 ID，例如 lerobot/pusht',
      job_option: '{name} · {family} · {count} ep',
      job_option_paused: '（已暂停）',
      job_meta: '{family} · {schema} · {scope} · {status}',
      job_banner_sub: '{family} · {schema} · {count} 条 episode',
      job_scope_full: '全量',
      job_scope_sample: '抽样',
      job_status_open: '进行中',
      job_status_paused: '已暂停',
      catalog_advanced_path: '高级路径',
      catalog_hide_advanced: '使用目录选择',
      catalog_collection_option: '{title} · {count} 个子集',
      connect_local_required: '请填写数据集目录。',
      connect_loading: '正在加载数据集...',
      connect_loaded: 'Loaded {count} episodes.',
      connect_episode_saved: 'Episode {idx} saved.',
      episodes_title: 'Episodes',
      episode_search: 'Filter #',
      episode_filter_all: 'All',
      episode_filter_complete: 'Complete',
      episode_filter_partial: 'In progress',
      episode_filter_none: 'Not started',
      episode_progress_summary: '{complete} / {total} complete · {partial} in progress',
      episode_archive_summary: '高级项（可选）',
      episode_archive_hint: '成功搬箱数由逐箱结果自动汇总；技能复核仅供组长抽检。标注员通常无需展开本节。',
      advanced_hl_subsection: '高层对话',
      episode_field_auto_suffix: '（自动）',
      episode_status_complete: 'Done',
      episode_status_partial: 'Draft',
      episode_status_none: 'New',
      soft_warn_W_EP_01: '结果为 fail，但成功搬箱数 > 0',
      soft_warn_W_EP_02: '结果为 success，但成功搬箱数为 0',
      soft_warn_W_EP_03: '结果为 fail，但有箱标记为成功',
      soft_warn_W_EP_04: '结果为 success，但有箱标记为失败',
      soft_warn_W_EP_05: '成功搬箱数与逐箱成功勾选不一致',
      soft_warn_W_EP_06: '未选择片段结果（outcome）',
      soft_warn_W_EP_07: '部分逐箱结果未填写 success',
      episode_select: 'Select an episode',
      episode_title: 'Episode {idx}',
      episode_meta: '{frames} frames @ {fps} fps • {duration}',
      outcome_aria_label: 'Episode outcome',
      skill_auto_badge: 'L1 · auto',
      btn_clear: '清空',
      task_label: '任务',
      timeline_labels: '时间轴标签',
      label_count: '标签（{n}）',
      label_hint_default: '先选标签，在时间轴空白处拖拽新建；拖色块左右边调整起止，拖中间可平移',
      label_hint_selected: '{id}: {hint}',
      label_hint_pick: '请先从 8 类固定标签中选择一项',
      label_hint_overlap: '与已有分段重叠，请调整起止帧',
      timeline_resize_start: '拖动调整起始帧',
      timeline_resize_end: '拖动调整结束帧',
      timeline_playhead_scrub: '拖动红线扫帧，视频画面同步跳转',
      regions_title: '分段列表',
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
      export_hint: '导出训练用 Parquet；未标注的 episode 帧索引为 -1。',
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
      skill_derivation_title: '原子技能（自动）',
      skill_derivation_hint: '调整上方 8 类相位，此处即时更新 L1；若不对请改 L2 边界，保存时一并写入。',
      skill_derivation_no_phases: '请在时间轴标注相位，下方将实时显示推导的原子技能。',
      skill_derivation_incomplete: '请补全或调整相位（reach → … → release），编辑时 L1 会同步更新。',
      skill_derivation_warnings: '推导提示',
      cycle_fields_title: '逐箱结果',
      cycle_fields_hint: '由 L2 相位自动判定；仅需修正异常并选择失败原因。',
      cycle_success_auto: 'auto',
      cycle_success_manual: 'manual',
      cycle_row_title: '第 {id} 箱 · {start}s–{end}s · {struct}',
      cycle_struct_complete: '结构完整',
      cycle_struct_incomplete: '结构不完整',
      cycle_success_unset: '未填',
      cycle_success_yes: '成功',
      cycle_success_no: '失败',
      enum_cycle_fail_reason_none: '—',
      enum_cycle_fail_reason_slip_grasp: '抓取滑脱',
      enum_cycle_fail_reason_drop_mid_transport: '搬运中途掉箱',
      enum_cycle_fail_reason_place_offset: '放置偏移',
      enum_cycle_fail_reason_incomplete: '周期不完整',
      enum_cycle_fail_reason_other: '其他',
      skill_cycle_label: '第 {id} 箱',
      enum_skill_review_pending: '待复核',
      enum_skill_review_approved: '已通过',
      enum_skill_review_rejected: '需修正',
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
    if (IS_DATALAB_EMBED) return 'zh-Hans';
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
    setLabelText(document.getElementById('collectionLabel'), t('label_collection'));
    setLabelText(document.getElementById('packageLabel'), t('label_package'));
    setLabelText(document.getElementById('jobLabel'), t('label_job'));
    setLabelText(document.getElementById('localLabel'), t('label_local_path'));
    setLabelText(document.getElementById('revisionLabel'), t('label_revision'));

    const loadBtn = document.querySelector('#connectForm button[type="submit"]');
    if (loadBtn) loadBtn.textContent = t('btn_load');

    const epTitle = document.getElementById('episodesTitle') || document.querySelector('.ls-sidebar-head h2');
    if (epTitle) epTitle.textContent = t('episodes_title');
    const epSearch = document.getElementById('episodeSearch');
    if (epSearch) epSearch.placeholder = t('episode_search');
    const epStatusFilter = document.getElementById('episodeStatusFilter');
    if (epStatusFilter) {
      const opts = epStatusFilter.options;
      if (opts[0]) opts[0].textContent = t('episode_filter_all');
      if (opts[1]) opts[1].textContent = t('episode_filter_complete');
      if (opts[2]) opts[2].textContent = t('episode_filter_partial');
      if (opts[3]) opts[3].textContent = t('episode_filter_none');
    }
    const episodeAdvancedSummary = document.getElementById('episodeAdvancedSummary');
    if (episodeAdvancedSummary) episodeAdvancedSummary.textContent = t('episode_archive_summary');
    const episodeAdvancedHint = document.getElementById('episodeAdvancedHint');
    if (episodeAdvancedHint) episodeAdvancedHint.textContent = t('episode_archive_hint');
    const hlSubsectionTitle = document.getElementById('hlSubsectionTitle');
    if (hlSubsectionTitle) hlSubsectionTitle.textContent = t('advanced_hl_subsection');
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

    const regionsHead = document.getElementById('regionsTitle') || document.querySelector('.ls-region-list-head span');
    if (regionsHead) regionsHead.textContent = t('regions_title');
    const playheadHandleEl = document.getElementById('playheadHandle');
    if (playheadHandleEl) {
      playheadHandleEl.title = t('timeline_playhead_scrub');
      playheadHandleEl.setAttribute('aria-label', t('timeline_playhead_scrub'));
    }
    const outcomeLabel = document.querySelector('.ls-outcome-label');
    if (outcomeLabel) outcomeLabel.textContent = t('outcome_label');
    const outcomeGroup = document.getElementById('outcomeGroup');
    if (outcomeGroup) outcomeGroup.setAttribute('aria-label', t('outcome_aria_label'));

    setLabelText(document.getElementById('hlStartLabel'), t('hl_start'));
    setLabelText(document.getElementById('hlEndLabel'), t('hl_end'));
    setLabelText(document.getElementById('hlUserLabel'), t('hl_user'));
    setLabelText(document.getElementById('hlRobotLabel'), t('hl_robot'));
    const hlSetStart = document.getElementById('hlSetStart');
    if (hlSetStart) hlSetStart.textContent = t('hl_set_start');
    const hlSetEnd = document.getElementById('hlSetEnd');
    if (hlSetEnd) hlSetEnd.textContent = t('hl_set_end');
    const addHighLevel = document.getElementById('addHighLevel');
    if (addHighLevel) addHighLevel.textContent = t('hl_add');

    const skillAutoBadge = document.getElementById('skillAutoBadge');
    if (skillAutoBadge) skillAutoBadge.textContent = t('skill_auto_badge');

    const exportSummary = document.getElementById('exportSummary');
    if (exportSummary) exportSummary.textContent = t('export_title');
    const exportHint = document.getElementById('exportHint');
    if (exportHint) exportHint.textContent = t('export_hint');
    setLabelText(document.getElementById('exportOutputLabel'), t('export_output'));
    const outputDir = document.getElementById('outputDir');
    if (outputDir) outputDir.placeholder = t('export_output_ph');
    const copyVideosText = document.getElementById('copyVideosText');
    if (copyVideosText) copyVideosText.textContent = t('export_copy_videos');
    const exportBtn = document.getElementById('exportBtn');
    if (exportBtn) exportBtn.textContent = t('export_btn');

    const hlDetails = document.getElementById('episodeAdvancedSummary');
    if (hlDetails) hlDetails.textContent = t('episode_archive_summary');

    const skillTitle = document.getElementById('skillDerivationTitle');
    if (skillTitle) skillTitle.textContent = t('skill_derivation_title');
    const skillHint = document.getElementById('skillDerivationHint');
    if (skillHint) skillHint.textContent = t('skill_derivation_hint');
    const cycleFieldsTitle = document.getElementById('cycleFieldsTitle');
    if (cycleFieldsTitle) cycleFieldsTitle.textContent = t('cycle_fields_title');
    const cycleFieldsHint = document.getElementById('cycleFieldsHint');
    if (cycleFieldsHint) cycleFieldsHint.textContent = t('cycle_fields_hint');

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
