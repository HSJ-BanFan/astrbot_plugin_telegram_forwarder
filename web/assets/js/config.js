export const MSG_TYPES = ["文字", "图片", "视频", "音频", "文件"];
export const TRI_STATE = ["继承全局", "开启", "关闭"];

export const FORWARD_GROUPS = [
  {
    id: "schedule",
    label: "调度",
    fields: [
      { key: "check_interval", label: "检测间隔", type: "int", suffix: "秒", defaultValue: 60 },
      { key: "send_interval", label: "发送间隔", type: "int", suffix: "秒", defaultValue: 60 },
      { key: "batch_size_limit", label: "单次发送批次上限", type: "int", defaultValue: 3 },
      { key: "retention_period", label: "消息保留时间", type: "int", suffix: "秒", defaultValue: 86400 },
      { key: "curfew_time", label: "宵禁时间段", type: "text", placeholder: "23:00-07:00", defaultValue: "" },
    ],
  },
  {
    id: "content",
    label: "内容",
    fields: [
      { key: "forward_types", label: "转发消息类型", type: "checks", options: MSG_TYPES, defaultValue: MSG_TYPES },
      { key: "max_file_size", label: "文件大小限制", type: "float", suffix: "MB", defaultValue: 0 },
      { key: "use_channel_title", label: "显示频道标题", type: "bool", defaultValue: true },
      { key: "exclude_text_on_media", label: "媒体消息仅发送媒体", type: "bool", defaultValue: false },
      { key: "strip_markdown_links", label: "剥离 Markdown 链接", type: "bool", defaultValue: false },
      { key: "filter_spoiler_messages", label: "过滤遮罩/剧透消息", type: "bool", defaultValue: false },
    ],
  },
  {
    id: "qq",
    label: "QQ 发送",
    fields: [
      { key: "qq_merge_threshold", label: "QQ 大合并阈值", type: "int", defaultValue: 0 },
      { key: "album_settle_seconds", label: "相册等待完整", type: "int", suffix: "秒", defaultValue: 8 },
      { key: "album_lookahead_limit", label: "相册补拉上限", type: "int", defaultValue: 20 },
      {
        key: "qq_big_merge_mode",
        label: "QQ 大合并范围",
        type: "select",
        options: ["独立频道", "混合所有频道", "关闭"],
        defaultValue: "独立频道",
      },
      {
        key: "apk_fallback_mode",
        label: "APK 发送失败兜底",
        type: "select",
        options: ["关闭", "直链", "压缩包", "直链优先，失败转压缩包"],
        defaultValue: "直链优先，失败转压缩包",
      },
      { key: "apk_direct_link_base_url", label: "APK 直链基址", type: "text", defaultValue: "" },
      { key: "file_direct_link_base_url", label: "普通文件直链基址", type: "text", defaultValue: "" },
      { key: "qq_send_logical_unit_budget", label: "QQ 单轮发送预算", type: "int", defaultValue: 0 },
      { key: "qq_target_fail_fast_consecutive_failures", label: "QQ 连续失败快停阈值", type: "int", defaultValue: 3 },
      { key: "target_circuit_fail_threshold", label: "QQ 目标熔断阈值", type: "int", defaultValue: 3 },
      { key: "target_circuit_cooldown_sec", label: "QQ 目标熔断冷却", type: "int", suffix: "秒", defaultValue: 300 },
      { key: "send_result_strict_ack", label: "严格 ACK 移除队列", type: "bool", defaultValue: false },
    ],
  },
  {
    id: "filters",
    label: "过滤监听",
    fields: [
      { key: "enable_deduplication", label: "转发查重", type: "bool", defaultValue: true },
      { key: "filter_keywords", label: "过滤关键词", type: "list", defaultValue: [] },
      { key: "filter_regex", label: "正则过滤", type: "text", defaultValue: "" },
      { key: "ai_filter_enabled", label: "AI 内容过滤", type: "bool", defaultValue: false },
      { key: "ai_filter_base_url", label: "AI Base URL", type: "text", placeholder: "https://api.example.com/v1", defaultValue: "" },
      { key: "ai_filter_allow_private_endpoint", label: "允许本地/私网 AI 端点", type: "bool", defaultValue: false },
      { key: "ai_filter_api_key", label: "AI API Key", type: "password", defaultValue: "" },
      { key: "ai_filter_model", label: "AI 模型名", type: "text", defaultValue: "" },
      {
        key: "ai_filter_prompt",
        label: "AI 过滤提示词",
        type: "textarea",
        defaultValue: "判断消息文字和图片是否应该过滤。重点过滤：\n1. NSFW、色情、裸露或明显性暗示内容；\n2. 二维码推广，尤其是网贷、借款、博彩、色情、诈骗类二维码；\n3. 诱导扫码、贷款秒到账、虚假金融推广等风险信息。\n正常新闻、普通聊天、技术文档和无风险二维码不要过滤。",
      },
      { key: "ai_filter_timeout", label: "AI 超时", type: "int", suffix: "秒", defaultValue: 20 },
      { key: "ai_filter_max_calls_per_cycle", label: "单轮 AI 分析上限", type: "int", suffix: "条", defaultValue: 5 },
      { key: "qr_filter_enabled", label: "本地二维码过滤", type: "bool", defaultValue: false },
      { key: "qr_filter_mode", label: "二维码过滤模式", type: "select", options: ["风险二维码", "全部二维码"], defaultValue: "风险二维码" },
      { key: "qr_risk_keywords", label: "二维码风险词", type: "list", defaultValue: ["loan", "借款", "贷款", "网贷", "秒到账", "博彩", "赌博", "色情", "约炮", "裸聊"] },
      { key: "content_filter_max_image_mb", label: "分析图片上限", type: "int", suffix: "MB", defaultValue: 5 },
      { key: "monitor_keywords", label: "监听关键词", type: "list", defaultValue: [] },
      { key: "monitor_regex", label: "监听正则", type: "text", defaultValue: "" },
    ],
  },
  {
    id: "retry",
    label: "重试会话",
    fields: [
      { key: "pending_retry_base_delay_sec", label: "失败重试基础退避", type: "int", suffix: "秒", defaultValue: 60 },
      { key: "pending_retry_max_delay_sec", label: "失败重试最大退避", type: "int", suffix: "秒", defaultValue: 1800 },
      { key: "wrong_session_rebuild_enabled", label: "会话异常自动重建", type: "bool", defaultValue: true },
    ],
  },
];

export const FORWARD_FIELDS = FORWARD_GROUPS.flatMap((group) => group.fields);
export const CHANNEL_GROUPS = [
  { id: "base", label: "基础" },
  { id: "content", label: "内容" },
  { id: "filters", label: "过滤监听" },
  { id: "targets", label: "目标" },
];
export const MERGE_RULE_CLASSES = [
  { value: "KeywordNextNMerge", label: "关键词后 N 条合并" },
  { value: "SomeACGPreviewPlusOriginal", label: "SomeACG 预览图+原图" },
];
