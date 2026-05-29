/**
 * One-off merge script: adds hotkeys.* Chinese strings to zh-Hans/common.json
 * Run from repo root: node web/apps/labelstudio/scripts/merge-hotkeys-zh.mjs
 */
import { readFileSync, writeFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const commonPath = join(__dirname, "../src/i18n/locales/zh-Hans/common.json");

const hotkeysZh = {
  modal: {
    title: "快捷键",
    heading: "快捷键",
    intro: "查看所有可用快捷键。",
    customize: "自定义",
  },
  sections: {
    settings: { title: "设置", description: "" },
    annotation: {
      title: "标注操作",
      description: "提交、跳过、撤销与重做等常用标注快捷键",
    },
    data_manager: {
      title: "数据管理器",
      description: "在项目数据管理器中浏览与管理任务的快捷键",
    },
    regions: {
      title: "区域管理",
      description: "创建、选择与编辑标注区域的快捷键",
    },
    tools: {
      title: "工具",
      description: "图像标注时工具面板的快捷键",
    },
    audio: {
      title: "音频控制",
      description: "音频播放与导航",
    },
    video: {
      title: "视频控制",
      description: "视频播放与导航",
    },
    timeseries: {
      title: "时间序列",
      description: "调整时间序列数据区域的快捷键",
    },
    image_gallery: {
      title: "图像画廊",
      description: "在多图任务中切换图像",
    },
    paragraphs: {
      title: "段落导航",
      description: "在段落/对话视图中切换短语与区域",
    },
  },
  header: {
    import: "导入",
    export: "导出",
    reset_defaults: "恢复默认",
  },
  ui: {
    save: "保存",
    empty_section: "此分组暂无快捷键",
    record_keys_aria: "点击以录制快捷键",
    press_keys_now: "请按下按键…",
    click_set_shortcut: "点击设置快捷键",
    apply: "应用",
    disable: "禁用",
    enable: "启用",
    click_edit: "点击编辑快捷键",
    cancel: "取消",
  },
  toast: {
    section_saved: "{{section}} 的快捷键已保存",
    save_failed: "保存失败：{{error}}",
    save_error: "保存出错：{{error}}",
    import_ok: "快捷键导入成功",
    import_error: "导入快捷键出错：{{error}}",
  },
  duplicate: {
    title: "警告：快捷键冲突",
    lead: '快捷键组合 "{{key}}" 已被以下功能占用：',
    warning_body: "重复的快捷键可能导致冲突或意外行为。确定要继续吗？",
    allow: "仍要使用",
  },
  errors: {
    unknown: "未知错误",
    import_save_failed: "无法保存导入的快捷键",
  },
  hook: {
    load_failed: "无法从服务器加载自定义快捷键，已使用本地缓存。",
    reset_confirm_title: "恢复默认快捷键？",
    reset_confirm_body:
      "确定要将所有快捷键与相关设置恢复为默认值吗？此操作不可撤销。",
    reset_ok: "恢复默认",
    reset_success: "所有快捷键与设置已恢复默认并已保存",
    reset_save_failed: "保存重置失败：{{error}}",
    reset_exception: "重置快捷键出错：{{error}}",
    save_failed_save: "保存快捷键失败",
    save_failed_reset: "重置快捷键失败",
    invalid_reset: "无效的重置请求",
    invalid_save: "快捷键配置无效",
    auth_required: "需要登录",
    server_error: "服务器错误，请稍后重试",
    network_error: "网络错误，请检查连接",
    export_success: "快捷键已导出",
  },
  import: {
    title: "导入快捷键",
    description:
      "在下方粘贴已导出的快捷键 JSON，将覆盖当前快捷键。请确保 JSON 中包含所需字段的快捷键对象数组。",
    json_label: "快捷键 JSON",
    placeholder:
      '[{"id": "1", "section": "annotation", "element": "annotation:submit", "label": "保存", "key": "ctrl+enter"}]',
    error_title: "导入错误",
    confirm: "导入快捷键",
    invalid_hotkey_object: "无效的快捷键对象",
    missing_fields: "缺少必填字段：{{fields}}",
    error_enter_json: "请输入要导入的 JSON",
    invalid_hotkeys_array: "格式无效：hotkeys 必须为数组",
    invalid_format: "格式无效：应为快捷键数组，或包含 hotkeys 属性的对象",
    no_hotkeys: "导入的数据中没有任何快捷键",
    hotkey_index_error: "第 {{index}} 条快捷键：{{message}}",
    json_parse_error: "JSON 解析失败，请检查语法",
  },
  items: {
    annotation_submit: { label: "提交标注", description: "提交当前标注" },
    annotation_skip: { label: "跳过任务", description: "跳过当前任务" },
    annotation_undo: { label: "撤销", description: "撤销上一步操作" },
    annotation_redo: { label: "重做", description: "重做已撤销的操作" },
    dm_focus_previous: { label: "聚焦上一任务", description: "将焦点移到上一个任务" },
    dm_focus_next: { label: "聚焦下一任务", description: "将焦点移到下一个任务" },
    dm_close_labeling: { label: "聚焦已完成任务", description: "聚焦「已完成」列" },
    dm_open_labeling: { label: "聚焦进行中任务", description: "聚焦「进行中」列" },
    dm_toggle_bulk_sidebar_minimization: {
      label: "切换批量侧边栏",
      description: "收起或展开批量操作侧边栏",
    },
    region_delete_all: { label: "删除全部区域", description: "移除所有区域" },
    region_focus: { label: "聚焦首个区域", description: "将焦点移到第一个可聚焦区域" },
    region_relation: { label: "创建区域关联", description: "在选中区域之间创建关联" },
    region_visibility: { label: "切换区域可见性", description: "显示或隐藏选中区域" },
    region_visibility_all: { label: "切换全部区域可见性", description: "显示或隐藏所有区域" },
    region_lock: { label: "锁定区域", description: "锁定或解锁选中区域" },
    region_meta: { label: "编辑区域元数据", description: "编辑选中区域的元数据" },
    region_unselect: { label: "取消选择区域", description: "取消当前选中的区域" },
    region_exit: { label: "退出区域模式", description: "退出关联模式并取消选择区域" },
    region_delete: { label: "删除选中区域", description: "删除当前选中的区域" },
    region_cycle: { label: "循环切换区域", description: "在所有区域间循环切换" },
    region_duplicate: { label: "复制区域", description: "复制选中区域" },
    segment_delete: { label: "删除片段", description: "删除选中的片段" },
    audio_back: { label: "后退 1 秒", description: "将音频后退 1 秒" },
    audio_playpause: { label: "播放 / 暂停音频", description: "切换音频播放状态" },
    audio_step_backward: { label: "上一帧", description: "后退一帧" },
    audio_step_forward: { label: "下一帧", description: "前进一帧" },
    media_playpause: { label: "播放 / 暂停视频", description: "切换视频播放状态" },
    media_step_backward: { label: "上一帧", description: "后退一帧" },
    media_step_forward: { label: "下一帧", description: "前进一帧" },
    video_keyframe_backward: { label: "上一关键帧", description: "跳转到上一关键帧" },
    video_keyframe_forward: { label: "下一关键帧", description: "跳转到下一关键帧" },
    video_backward: { label: "快退", description: "向后跳转" },
    video_rewind: { label: "第一帧", description: "跳转到第一帧" },
    video_forward: { label: "快进", description: "向前跳转" },
    video_fastforward: { label: "最后一帧", description: "跳转到最后一帧" },
    video_hop_backward: { label: "大幅后退", description: "快速向后跳转" },
    video_hop_forward: { label: "大幅前进", description: "快速向前跳转" },
    ts_grow_left: { label: "向左扩展", description: "将区域向左扩展" },
    ts_grow_right: { label: "向右扩展", description: "将区域向右扩展" },
    ts_shrink_left: { label: "从左侧收缩", description: "从左侧缩小区域" },
    ts_shrink_right: { label: "从右侧收缩", description: "从右侧缩小区域" },
    ts_grow_left_large: { label: "向左大幅扩展", description: "显著向左扩展区域" },
    ts_grow_right_large: { label: "向右大幅扩展", description: "显著向右扩展区域" },
    ts_shrink_left_large: { label: "从左侧大幅收缩", description: "从左侧显著缩小区域" },
    ts_shrink_right_large: { label: "从右侧大幅收缩", description: "从右侧显著缩小区域" },
    image_prev: { label: "上一张图", description: "查看上一张图像" },
    image_next: { label: "下一张图", description: "查看下一张图像" },
    tool_zoom_in: { label: "放大", description: "放大图像" },
    tool_pan_image: { label: "平移图像", description: "平移浏览图像" },
    tool_zoom_to_fit: { label: "适应窗口", description: "缩放以完整显示图像" },
    tool_zoom_to_actual: { label: "100% 显示", description: "缩放到实际尺寸（100%）" },
    tool_zoom_out: { label: "缩小", description: "缩小图像" },
    tool_move: { label: "移动工具", description: "移动工具以调整标注位置" },
    tool_brush: { label: "画笔工具", description: "选择画笔工具" },
    tool_ellipse: { label: "椭圆工具", description: "选择椭圆工具" },
    tool_eraser: { label: "橡皮擦工具", description: "选择橡皮擦工具" },
    tool_auto_detect: { label: "自动检测", description: "使用自动检测建议区域" },
    tool_key_point: { label: "关键点工具", description: "选择关键点标注工具" },
    tool_magic_wand: { label: "魔棒工具", description: "智能选取区域的魔棒工具" },
    tool_polygon: { label: "多边形工具", description: "选择多边形标注工具" },
    tool_rect: { label: "矩形工具", description: "选择矩形标注工具" },
    tool_rect_3point: { label: "三点矩形", description: "通过三点绘制旋转矩形" },
    tool_rotate_left: { label: "向左旋转", description: "将图像逆时针旋转 90°" },
    tool_rotate_right: { label: "向右旋转", description: "将图像顺时针旋转 90°" },
    tool_decrease_tool: { label: "减小工具尺寸", description: "减小工具尺寸" },
    tool_increase_tool: { label: "增大工具尺寸", description: "增大工具尺寸" },
    phrases_next_phrase: { label: "下一句", description: "在段落视图中跳到下一句" },
    phrases_previous_phrase: { label: "上一句", description: "在段落视图中跳到上一句" },
    phrases_select_all_annotate: {
      label: "全选并标注",
      description: "选中当前句全部文本并创建标注",
    },
    phrases_next_region: { label: "句内下一区域", description: "在当前句内跳到下一个区域" },
    phrases_previous_region: { label: "句内上一区域", description: "在当前句内跳到上一个区域" },
  },
};

const raw = readFileSync(commonPath, "utf8");
const json = JSON.parse(raw);
json.hotkeys = hotkeysZh;
writeFileSync(commonPath, `${JSON.stringify(json, null, 2)}\n`, "utf8");
console.log("Merged hotkeys into", commonPath);
