const MSG_TYPES = ["文字", "图片", "视频", "音频", "文件"];
const TRI_STATE = ["继承全局", "开启", "关闭"];

const FORWARD_GROUPS = [
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

const FORWARD_FIELDS = FORWARD_GROUPS.flatMap((group) => group.fields);
const CHANNEL_GROUPS = [
  { id: "base", label: "基础" },
  { id: "content", label: "内容" },
  { id: "filters", label: "过滤监听" },
  { id: "targets", label: "目标" },
];
const MERGE_RULE_CLASSES = [
  { value: "KeywordNextNMerge", label: "关键词后 N 条合并" },
  { value: "SomeACGPreviewPlusOriginal", label: "SomeACG 预览图+原图" },
];
const DEFAULT_WEB_CONFIG = { enabled: true, host: "127.0.0.1", port: 8180, token: "" };

const els = {};
const state = {
  token: localStorage.getItem("telegram_forwarder_token") || "",
  config: null,
  status: null,
  section: "overview",
  forwardGroup: "schedule",
  expandedChannels: new Set(),
  channelGroups: {},
  expandedMergeRules: new Set(),
};

function $(id) {
  return document.getElementById(id);
}

function cacheElements() {
  [
    "authScreen",
    "authForm",
    "tokenInput",
    "authError",
    "appShell",
    "sidebar",
    "sidebarScrim",
    "mobileMenu",
    "logoutBtn",
    "refreshBtn",
    "saveBtn",
    "sectionTitle",
    "toast",
    "telegramStatus",
    "schedulerStatus",
    "channelCount",
    "queueCount",
    "queueList",
    "runtimeMessage",
    "loginBadge",
    "loginMessage",
    "loginAccountCard",
    "loginAccountInfo",
    "apiIdInput",
    "apiHashInput",
    "phoneInput",
    "proxyInput",
    "codeInput",
    "passwordInput",
    "sendCodeBtn",
    "submitCodeBtn",
    "submitPasswordBtn",
    "resetLoginBtn",
    "targetChannelInput",
    "targetQQInput",
    "debugDefaultInput",
    "addChannelBtn",
    "channelList",
    "addMergeRuleBtn",
    "mergeRuleList",
    "forwardTabs",
    "forwardConfigForm",
    "webEnabledInput",
    "webHostInput",
    "webPortInput",
    "webTokenInput",
    "exportConfigBtn",
    "importConfigBtn",
    "configImportFile",
    "exportSessionBtn",
    "importSessionBtn",
    "sessionImportFile",
    "rawConfigInput",
    "saveRawBtn",
    "runCheckBtn",
    "pauseBtn",
    "resumeBtn",
    "clearQueueBtn",
  ].forEach((id) => {
    els[id] = $(id);
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => els.toast.classList.remove("show"), 2800);
}

async function api(path, options = {}) {
  const headers = { "X-Admin-Token": state.token, ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, {
    ...options,
    headers,
    body: options.body && !(options.body instanceof FormData) ? JSON.stringify(options.body) : options.body,
  });
  const payload = await response.json().catch(() => ({ ok: false, message: "响应不是 JSON。" }));
  if (!response.ok || !payload.ok) {
    throw new Error(payload.message || `请求失败：${response.status}`);
  }
  return payload.data || {};
}

async function checkToken(token) {
  const response = await fetch("/api/auth/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  const payload = await response.json();
  return Boolean(payload?.data?.authorized);
}

function splitList(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(String).map((v) => v.trim()).filter(Boolean);
  return String(value).split(/[\n,]/).map((v) => v.trim()).filter(Boolean);
}

function joinList(value) {
  return Array.isArray(value) ? value.join("\n") : "";
}

function intValue(id, fallback = 0) {
  const parsed = Number.parseInt(els[id].value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function currentWebConfig() {
  return { ...DEFAULT_WEB_CONFIG, ...(state.config?.web_config || {}) };
}

function renderStatus() {
  const status = state.status || {};
  const telegram = status.telegram || {};
  const runtime = status.runtime || {};
  const queue = status.queue || {};
  const me = telegram.me;

  els.telegramStatus.textContent = telegram.authorized
    ? `已授权${me?.username ? ` @${me.username}` : ""}`
    : telegram.connected
      ? "已连接，未授权"
      : "未连接";
  els.schedulerStatus.textContent = runtime.scheduler_running
    ? runtime.paused
      ? "已暂停"
      : "运行中"
    : "未启动";
  els.channelCount.textContent = String(status.channels?.count ?? 0);
  els.queueCount.textContent = String(queue.total ?? 0);
  els.loginBadge.textContent = telegram.authorized
    ? telegram.replace_existing
      ? "准备重新登录"
      : "已登录"
    : telegram.login_in_progress
      ? "登录中"
      : "未登录";

  const accountTitle = telegram.authorized
    ? me?.username
      ? `@${me.username}`
      : me?.phone || "已授权账号"
    : "尚未登录 Telegram";
  const accountDetail = telegram.authorized
    ? [me?.first_name, me?.last_name].filter(Boolean).join(" ") || me?.phone || "Session 可用"
    : telegram.login_in_progress
      ? telegram.replace_existing && !telegram.phone
        ? "准备重新登录，当前账号仍然保留"
        : `验证码流程进行中：${telegram.phone || "-"}`
      : "完成登录后将自动启动转发任务";
  const loginRows = telegram.authorized
    ? [
        ["账号", accountTitle],
        ["姓名", [me?.first_name, me?.last_name].filter(Boolean).join(" ") || "-"],
        ["手机号", me?.phone || telegram.phone || "-"],
        ["用户 ID", me?.id || "-"],
        ["连接状态", telegram.connected ? "已连接" : "未连接"],
      ]
    : [
        ["状态", telegram.login_in_progress ? "登录流程中" : "未登录"],
        ["手机号", telegram.phone || "-"],
      ];
  els.loginAccountInfo.innerHTML = `
    <span class="account-pill">${escapeHtml(accountTitle)}</span>
    <span>${escapeHtml(accountDetail)}</span>
    <div class="account-detail-grid">
      ${loginRows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
    </div>
  `;
  els.loginAccountCard.hidden = Boolean(telegram.replace_existing);

  updateLoginSteps();

  const entries = Object.entries(queue.by_channel || {});
  els.queueList.innerHTML = entries.length
    ? entries
        .map(([channel, count]) => `<div class="queue-item"><span>${escapeHtml(channel)}</span><strong>${count}</strong></div>`)
        .join("")
    : '<div class="queue-item"><span>无待发送消息</span><strong>0</strong></div>';
}

function updateLoginSteps() {
  const telegram = state.status?.telegram || {};
  const steps = document.querySelectorAll("[data-login-step]");
  steps.forEach((step) => {
    step.classList.remove("active", "done");
    step.hidden = true;
  });

  if (telegram.authorized && !telegram.replace_existing) {
    els.loginMessage.textContent = "Telegram 已登录。需要切换账号时点击重新登录，新账号成功前不会清除当前登录。";
    els.resetLoginBtn.hidden = false;
    return;
  }

  els.resetLoginBtn.hidden = !telegram.authorized && !telegram.login_in_progress;
  const connectStep = document.querySelector('[data-login-step="connect"]');
  const codeStep = document.querySelector('[data-login-step="code"]');
  const passwordStep = document.querySelector('[data-login-step="password"]');
  connectStep.hidden = false;
  connectStep.classList.add(telegram.code_sent ? "done" : "active");

  if (telegram.login_in_progress) {
    if (telegram.replace_existing) {
      connectStep.hidden = false;
      if (!telegram.code_sent) {
        connectStep.classList.remove("done");
        connectStep.classList.add("active");
        els.loginMessage.textContent = "当前账号仍然保留。填写新账号手机号并发送验证码，新账号成功后才会替换当前登录。";
        return;
      }
    }
    if (!telegram.code_sent) {
      els.loginMessage.textContent = "填写连接信息并发送验证码。";
      return;
    }
    codeStep.hidden = false;
    codeStep.classList.add(telegram.need_password ? "done" : "active");
    els.loginMessage.textContent = telegram.need_password ? "验证码已通过，请继续提交两步验证密码。" : "验证码已发送，请输入 Telegram 收到的验证码。";
    if (telegram.need_password) {
      passwordStep.hidden = false;
      passwordStep.classList.add("active");
    }
  } else {
    els.loginMessage.textContent = "按顺序配置连接信息、发送验证码、提交验证码。";
  }
}

function renderRootConfig() {
  const cfg = state.config || {};
  els.apiIdInput.value = cfg.api_id || "";
  els.apiHashInput.value = cfg.api_hash || "";
  els.phoneInput.value = cfg.phone || "";
  els.proxyInput.value = cfg.proxy || "";
  els.targetChannelInput.value = cfg.target_channel || "";
  els.targetQQInput.value = joinList(cfg.target_qq_session || []);
  els.debugDefaultInput.checked = Boolean(cfg.debug_enabled_default);

  const web = currentWebConfig();
  els.webEnabledInput.checked = Boolean(web.enabled);
  els.webHostInput.value = web.host;
  els.webPortInput.value = web.port;
  els.webTokenInput.value = web.token;
}

function renderForwardTabs() {
  els.forwardTabs.innerHTML = FORWARD_GROUPS.map(
    (group) =>
      `<button type="button" data-forward-tab="${group.id}" class="${state.forwardGroup === group.id ? "active" : ""}">${escapeHtml(group.label)}</button>`,
  ).join("");
  document.querySelectorAll("[data-forward-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      collectForwardConfig();
      state.forwardGroup = button.dataset.forwardTab;
      renderForwardTabs();
      renderForwardConfig();
    });
  });
}

function renderField(field, value, attrName) {
  const attr = `${attrName}="${field.key}"`;
  if (field.type === "bool") {
    return `
      <div class="settings-card">
        <h3>${escapeHtml(field.label)}</h3>
        <label class="toggle-row">
          <input ${attr} type="checkbox" ${value ? "checked" : ""} />
          <span>开启</span>
        </label>
      </div>
    `;
  }
  if (field.type === "select") {
    return `
      <div class="settings-card">
        <h3>${escapeHtml(field.label)}</h3>
        <select ${attr}>
          ${field.options.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
        </select>
      </div>
    `;
  }
  if (field.type === "checks") {
    const selected = Array.isArray(value) ? value : [];
    return `
      <div class="settings-card wide">
        <h3>${escapeHtml(field.label)}</h3>
        <div class="check-group" ${attr}>
          ${field.options
            .map(
              (option) => `
                <label class="check-pill">
                  <input type="checkbox" value="${escapeHtml(option)}" ${selected.includes(option) ? "checked" : ""} />
                  ${escapeHtml(option)}
                </label>
              `,
            )
            .join("")}
        </div>
      </div>
    `;
  }
  if (field.type === "list") {
    return `
      <div class="settings-card">
        <h3>${escapeHtml(field.label)}</h3>
        <textarea ${attr} rows="4">${escapeHtml(joinList(value))}</textarea>
      </div>
    `;
  }
  const inputType = field.type === "int" || field.type === "float" ? "number" : "text";
  const step = field.type === "float" ? ' step="0.1"' : "";
  const placeholder = field.placeholder ? ` placeholder="${escapeHtml(field.placeholder)}"` : "";
  return `
    <div class="settings-card">
      <h3>${escapeHtml(field.label)}</h3>
      <input ${attr} type="${inputType}"${step}${placeholder} value="${escapeHtml(value ?? "")}" />
      ${field.suffix ? `<span class="field-hint">${escapeHtml(field.suffix)}</span>` : ""}
    </div>
  `;
}

function renderForwardConfig() {
  const cfg = state.config?.forward_config || {};
  const group = FORWARD_GROUPS.find((item) => item.id === state.forwardGroup) || FORWARD_GROUPS[0];
  els.forwardConfigForm.innerHTML = group.fields
    .map((field) => renderField(field, cfg[field.key] ?? field.defaultValue, "data-forward"))
    .join("");
}

function defaultMergeRule() {
  return {
    __template_key: "default",
    channel: "",
    rule_class: "KeywordNextNMerge",
    params: {
      trigger_keywords: [],
      trigger_regex: "",
      next_count: 2,
      time_window_seconds: 60,
    },
  };
}

function normalizeMergeRule(rule = {}) {
  const defaults = defaultMergeRule();
  return {
    ...defaults,
    ...rule,
    params: {
      ...defaults.params,
      ...(rule.params || {}),
    },
  };
}

function mergeRuleKey(rule, index) {
  const normalized = normalizeMergeRule(rule);
  return normalized.channel || normalized.rule_class ? `rule:${normalized.channel}:${normalized.rule_class}:${index}` : `idx:${index}`;
}

function mergeRuleTitle(rule) {
  const normalized = normalizeMergeRule(rule);
  const classLabel = MERGE_RULE_CLASSES.find((item) => item.value === normalized.rule_class)?.label || normalized.rule_class || "未选择规则";
  return normalized.channel ? `@${normalized.channel} · ${classLabel}` : `新规则 · ${classLabel}`;
}

function renderMergeRules() {
  const rules = Array.isArray(state.config?.merge_rules) ? state.config.merge_rules : [];
  els.mergeRuleList.innerHTML = rules.length
    ? rules
        .map((rule, index) => {
          const cfg = normalizeMergeRule(rule);
          const params = cfg.params || {};
          const key = mergeRuleKey(cfg, index);
          const collapsed = !state.expandedMergeRules.has(key);
          return `
            <article class="channel-card merge-card ${collapsed ? "collapsed" : ""}" data-merge-index="${index}" data-merge-key="${escapeHtml(key)}">
              <button class="channel-summary" type="button" data-toggle-merge="${index}">
                <div>
                  <div class="channel-title-text">${escapeHtml(mergeRuleTitle(cfg))}</div>
                  <div class="channel-meta">
                    <span>后续 ${escapeHtml(params.next_count ?? params.merge_count ?? 2)} 条</span>
                    <span>超时 ${escapeHtml(params.time_window_seconds ?? 60)} 秒</span>
                  </div>
                </div>
                <span class="chevron">⌄</span>
              </button>
              <div class="channel-body">
                <section class="channel-section active">
                  <div class="form-grid">
                    <label class="field">频道用户名<input data-merge-field="channel" value="${escapeHtml(cfg.channel)}" /></label>
                    <label class="field">规则类型
                      <select data-merge-field="rule_class">
                        ${MERGE_RULE_CLASSES.map((item) => `<option value="${escapeHtml(item.value)}" ${cfg.rule_class === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
                      </select>
                    </label>
                  </div>
                  <div class="form-grid">
                    <label class="field">触发关键词<textarea data-merge-param="trigger_keywords" rows="4">${escapeHtml(joinList(params.trigger_keywords || params.keywords || []))}</textarea></label>
                    <label class="field">触发正则<input data-merge-param="trigger_regex" value="${escapeHtml(params.trigger_regex || "")}" /></label>
                    <label class="field">后续消息数<input data-merge-param="next_count" type="number" min="1" value="${escapeHtml(params.next_count ?? params.merge_count ?? 2)}" /></label>
                    <label class="field">合并超时(秒)<input data-merge-param="time_window_seconds" type="number" min="1" value="${escapeHtml(params.time_window_seconds ?? 60)}" /></label>
                  </div>
                  <p class="field-hint">命中过滤词时，整个合并组会一起过滤；超时未凑够后续消息时，会按已经抓到的消息继续处理。</p>
                </section>
                <div class="channel-actions">
                  <button class="btn btn-soft danger" data-remove-merge="${index}" type="button">删除规则</button>
                </div>
              </div>
            </article>
          `;
        })
        .join("")
    : '<div class="queue-item"><span>暂无合并规则</span><strong>0</strong></div>';

  document.querySelectorAll("[data-toggle-merge]").forEach((button) => {
    button.addEventListener("click", () => {
      collectMergeRules();
      const card = button.closest(".merge-card");
      const key = card.dataset.mergeKey;
      if (state.expandedMergeRules.has(key)) {
        state.expandedMergeRules.delete(key);
      } else {
        state.expandedMergeRules.add(key);
      }
      renderMergeRules();
    });
  });
  document.querySelectorAll("[data-remove-merge]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const index = Number.parseInt(button.dataset.removeMerge, 10);
      collectMergeRules();
      state.config.merge_rules.splice(index, 1);
      renderMergeRules();
    });
  });
}

function defaultChannel() {
  return {
    __template_key: "default",
    channel_username: "",
    start_time: "",
    check_interval: 0,
    msg_limit: 10,
    priority: 0,
    exclude_text_on_media: "继承全局",
    forward_types: MSG_TYPES.slice(),
    max_file_size: 0,
    ignore_global_filters: false,
    filter_keywords: [],
    filter_regex: "",
    filter_spoiler_messages: "继承全局",
    strip_markdown_links: "继承全局",
    monitor_keywords: [],
    monitor_regex: "",
    target_qq_sessions: [],
  };
}

function triStateSelect(name, value) {
  return `
    <select data-channel-field="${name}">
      ${TRI_STATE.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
    </select>
  `;
}

function channelKey(channel, index) {
  return channel.channel_username ? `name:${channel.channel_username}` : `idx:${index}`;
}

function activeChannelGroup(key) {
  return state.channelGroups[key] || CHANNEL_GROUPS[0].id;
}

function renderChannels() {
  const channels = Array.isArray(state.config?.source_channels) ? state.config.source_channels : [];
  els.channelList.innerHTML = channels.length
    ? channels
        .map((channel, index) => {
          const cfg = { ...defaultChannel(), ...channel };
          const key = channelKey(cfg, index);
          const collapsed = !state.expandedChannels.has(key);
          const selectedTypes = Array.isArray(cfg.forward_types) ? cfg.forward_types : [];
          const title = cfg.channel_username ? `@${cfg.channel_username}` : "新频道";
          const group = activeChannelGroup(key);
          return `
            <article class="channel-card ${collapsed ? "collapsed" : ""}" data-channel-index="${index}" data-channel-key="${escapeHtml(key)}">
              <button class="channel-summary" type="button" data-toggle-channel="${index}">
                <div>
                  <div class="channel-title-text">${escapeHtml(title)}</div>
                  <div class="channel-meta">
                    <span>抓取 ${escapeHtml(cfg.msg_limit)} 条</span>
                    <span>间隔 ${escapeHtml(cfg.check_interval || "全局")} 秒</span>
                    <span>优先级 ${escapeHtml(cfg.priority || 0)}</span>
                  </div>
                </div>
                <span class="chevron">⌄</span>
              </button>
              <div class="channel-body">
                <div class="segmented channel-tabs">
                  ${CHANNEL_GROUPS.map(
                    (item) =>
                      `<button type="button" data-channel-tab="${index}" data-channel-tab-id="${item.id}" class="${group === item.id ? "active" : ""}">${escapeHtml(item.label)}</button>`,
                  ).join("")}
                </div>
                <section class="channel-section ${group === "base" ? "active" : ""}" data-channel-section="base">
                  <div class="form-grid">
                    <label class="field">频道用户名<input data-channel-field="channel_username" value="${escapeHtml(cfg.channel_username)}" /></label>
                    <label class="field">起始日期<input data-channel-field="start_time" value="${escapeHtml(cfg.start_time)}" placeholder="YYYY-MM-DD" /></label>
                    <label class="field">检测间隔<input data-channel-field="check_interval" type="number" value="${escapeHtml(cfg.check_interval)}" /></label>
                    <label class="field">单次抓取上限<input data-channel-field="msg_limit" type="number" value="${escapeHtml(cfg.msg_limit)}" /></label>
                    <label class="field">优先级<input data-channel-field="priority" type="number" value="${escapeHtml(cfg.priority)}" /></label>
                  </div>
                </section>
                <section class="channel-section ${group === "content" ? "active" : ""}" data-channel-section="content">
                  <div class="form-grid">
                    <label class="field">文件大小限制(MB)<input data-channel-field="max_file_size" type="number" step="0.1" value="${escapeHtml(cfg.max_file_size)}" /></label>
                    <label class="field">媒体文本${triStateSelect("exclude_text_on_media", cfg.exclude_text_on_media)}</label>
                    <label class="field">剧透过滤${triStateSelect("filter_spoiler_messages", cfg.filter_spoiler_messages)}</label>
                    <label class="field">Markdown 链接${triStateSelect("strip_markdown_links", cfg.strip_markdown_links)}</label>
                  </div>
                  <div class="check-group" data-channel-field="forward_types">
                    ${MSG_TYPES.map(
                      (type) => `
                        <label class="check-pill">
                          <input type="checkbox" value="${escapeHtml(type)}" ${selectedTypes.includes(type) ? "checked" : ""} />
                          ${escapeHtml(type)}
                        </label>
                      `,
                    ).join("")}
                  </div>
                </section>
                <section class="channel-section ${group === "filters" ? "active" : ""}" data-channel-section="filters">
                  <label class="toggle-row">
                    <input data-channel-field="ignore_global_filters" type="checkbox" ${cfg.ignore_global_filters ? "checked" : ""} />
                    <span>忽略全局文本过滤</span>
                  </label>
                  <div class="form-grid">
                    <label class="field">过滤关键词<textarea data-channel-field="filter_keywords" rows="4">${escapeHtml(joinList(cfg.filter_keywords))}</textarea></label>
                    <label class="field">监听关键词<textarea data-channel-field="monitor_keywords" rows="4">${escapeHtml(joinList(cfg.monitor_keywords))}</textarea></label>
                    <label class="field">过滤正则<input data-channel-field="filter_regex" value="${escapeHtml(cfg.filter_regex)}" /></label>
                    <label class="field">监听正则<input data-channel-field="monitor_regex" value="${escapeHtml(cfg.monitor_regex)}" /></label>
                  </div>
                </section>
                <section class="channel-section ${group === "targets" ? "active" : ""}" data-channel-section="targets">
                  <label class="field">专属 QQ 目标<textarea data-channel-field="target_qq_sessions" rows="4">${escapeHtml(joinList(cfg.target_qq_sessions))}</textarea></label>
                </section>
                <div class="channel-actions">
                  <button class="btn btn-soft danger" data-remove-channel="${index}" type="button">删除频道</button>
                </div>
              </div>
            </article>
          `;
        })
        .join("")
    : '<div class="queue-item"><span>暂无源频道</span><strong>0</strong></div>';

  document.querySelectorAll("[data-toggle-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      collectChannels();
      const card = button.closest(".channel-card");
      const key = card.dataset.channelKey;
      if (state.expandedChannels.has(key)) {
        state.expandedChannels.delete(key);
      } else {
        state.expandedChannels.add(key);
      }
      renderChannels();
    });
  });
  document.querySelectorAll("[data-channel-tab]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      collectChannels();
      const card = button.closest(".channel-card");
      const key = card.dataset.channelKey;
      state.channelGroups[key] = button.dataset.channelTabId;
      state.expandedChannels.add(key);
      renderChannels();
    });
  });
  document.querySelectorAll("[data-remove-channel]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const index = Number.parseInt(button.dataset.removeChannel, 10);
      collectChannels();
      state.config.source_channels.splice(index, 1);
      renderChannels();
    });
  });
}

function renderRawConfig() {
  els.rawConfigInput.value = JSON.stringify(state.config || {}, null, 2);
}

function renderAll() {
  renderStatus();
  renderRootConfig();
  renderForwardTabs();
  renderForwardConfig();
  renderChannels();
  renderMergeRules();
  renderRawConfig();
  updateTopbarActions();
}

function collectRootConfig() {
  state.config.api_id = intValue("apiIdInput", 0);
  state.config.api_hash = els.apiHashInput.value.trim();
  state.config.phone = els.phoneInput.value.trim();
  state.config.proxy = els.proxyInput.value.trim();
  state.config.target_channel = els.targetChannelInput.value.trim();
  state.config.target_qq_session = splitList(els.targetQQInput.value);
  state.config.debug_enabled_default = els.debugDefaultInput.checked;
  state.config.web_config = {
    enabled: els.webEnabledInput.checked,
    host: els.webHostInput.value.trim() || DEFAULT_WEB_CONFIG.host,
    port: intValue("webPortInput", DEFAULT_WEB_CONFIG.port),
    token: els.webTokenInput.value.trim(),
  };
}

function collectForwardConfig() {
  const cfg = { ...(state.config.forward_config || {}) };
  FORWARD_FIELDS.forEach((field) => {
    const el = document.querySelector(`[data-forward="${field.key}"]`);
    if (!el) return;
    if (field.type === "bool") {
      cfg[field.key] = el.checked;
    } else if (field.type === "checks") {
      cfg[field.key] = Array.from(el.querySelectorAll("input:checked")).map((item) => item.value);
    } else if (field.type === "list") {
      cfg[field.key] = splitList(el.value);
    } else if (field.type === "int") {
      const parsed = Number.parseInt(el.value, 10);
      cfg[field.key] = Number.isFinite(parsed) ? parsed : field.defaultValue;
    } else if (field.type === "float") {
      const parsed = Number.parseFloat(el.value);
      cfg[field.key] = Number.isFinite(parsed) ? parsed : field.defaultValue;
    } else {
      cfg[field.key] = el.value.trim();
    }
  });
  state.config.forward_config = cfg;
}

function channelField(card, name) {
  return card.querySelector(`[data-channel-field="${name}"]`);
}

function collectChannels({ keepEmpty = true } = {}) {
  state.config.source_channels = Array.from(document.querySelectorAll(".channel-card[data-channel-index]"))
    .map((card) => {
      const index = Number.parseInt(card.dataset.channelIndex, 10);
      const current = { ...defaultChannel(), ...(state.config.source_channels?.[index] || {}) };
      const getText = (name) => channelField(card, name)?.value?.trim() || "";
      const getInt = (name, fallback) => {
        const raw = channelField(card, name)?.value;
        if (raw == null) return fallback;
        const parsed = Number.parseInt(String(raw).trim(), 10);
        return Number.isFinite(parsed) ? parsed : fallback;
      };
      const getFloat = (name, fallback) => {
        const raw = channelField(card, name)?.value;
        if (raw == null) return fallback;
        const parsed = Number.parseFloat(String(raw).trim());
        return Number.isFinite(parsed) ? parsed : fallback;
      };
      const getList = (name, fallback) => {
        const field = channelField(card, name);
        if (!field) return fallback;
        return splitList(field.value);
      };
      const getChecks = (name, fallback) => {
        const field = channelField(card, name);
        if (!field) return fallback;
        return Array.from(field.querySelectorAll("input:checked")).map((item) => item.value);
      };
      const getBool = (name, fallback) => {
        const field = channelField(card, name);
        return field ? Boolean(field.checked) : fallback;
      };
      const getTri = (name, fallback) => getText(name) || fallback;
      const oldKey = card.dataset.channelKey;
      const collected = {
        __template_key: "default",
        channel_username: (getText("channel_username") || current.channel_username).replace(/^[@#]/, ""),
        start_time: channelField(card, "start_time") ? getText("start_time") : current.start_time,
        check_interval: getInt("check_interval", current.check_interval),
        msg_limit: getInt("msg_limit", current.msg_limit),
        priority: getInt("priority", current.priority),
        exclude_text_on_media: getTri("exclude_text_on_media", current.exclude_text_on_media),
        forward_types: getChecks("forward_types", current.forward_types),
        max_file_size: getFloat("max_file_size", current.max_file_size),
        ignore_global_filters: getBool("ignore_global_filters", current.ignore_global_filters),
        filter_keywords: getList("filter_keywords", current.filter_keywords),
        filter_regex: channelField(card, "filter_regex") ? getText("filter_regex") : current.filter_regex,
        filter_spoiler_messages: getTri("filter_spoiler_messages", current.filter_spoiler_messages),
        strip_markdown_links: getTri("strip_markdown_links", current.strip_markdown_links),
        monitor_keywords: getList("monitor_keywords", current.monitor_keywords),
        monitor_regex: channelField(card, "monitor_regex") ? getText("monitor_regex") : current.monitor_regex,
        target_qq_sessions: getList("target_qq_sessions", current.target_qq_sessions),
      };
      const newKey = channelKey(collected, index);
      if (oldKey && newKey !== oldKey) {
        if (state.expandedChannels.has(oldKey)) {
          state.expandedChannels.delete(oldKey);
          state.expandedChannels.add(newKey);
        }
        if (state.channelGroups[oldKey]) {
          state.channelGroups[newKey] = state.channelGroups[oldKey];
          delete state.channelGroups[oldKey];
        }
      }
      return collected;
    })
    .filter((channel) => keepEmpty || channel.channel_username);
}

function collectMergeRules({ keepEmpty = true } = {}) {
  const cards = Array.from(document.querySelectorAll(".merge-card[data-merge-index]"));
  if (!cards.length) {
    if (!Array.isArray(state.config.merge_rules)) {
      state.config.merge_rules = [];
    }
    return;
  }

  state.config.merge_rules = cards
    .map((card) => {
      const index = Number.parseInt(card.dataset.mergeIndex, 10);
      const current = normalizeMergeRule(state.config.merge_rules?.[index] || {});
      const field = (name) => card.querySelector(`[data-merge-field="${name}"]`);
      const param = (name) => card.querySelector(`[data-merge-param="${name}"]`);
      const getText = (node, fallback = "") => (node ? node.value.trim() : fallback);
      const getInt = (node, fallback) => {
        if (!node) return fallback;
        const parsed = Number.parseInt(String(node.value).trim(), 10);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
      };
      const collected = {
        __template_key: "default",
        channel: getText(field("channel"), current.channel).replace(/^[@#]/, ""),
        rule_class: getText(field("rule_class"), current.rule_class),
        params: {
          ...current.params,
          trigger_keywords: splitList(param("trigger_keywords")?.value || ""),
          trigger_regex: getText(param("trigger_regex"), current.params.trigger_regex || ""),
          next_count: getInt(param("next_count"), current.params.next_count || 2),
          time_window_seconds: getInt(param("time_window_seconds"), current.params.time_window_seconds || 60),
        },
      };
      const oldKey = card.dataset.mergeKey;
      const newKey = mergeRuleKey(collected, index);
      if (oldKey && newKey !== oldKey && state.expandedMergeRules.has(oldKey)) {
        state.expandedMergeRules.delete(oldKey);
        state.expandedMergeRules.add(newKey);
      }
      return collected;
    })
    .filter((rule) => keepEmpty || (rule.channel && rule.rule_class));
}

function collectForms() {
  collectRootConfig();
  collectForwardConfig();
  collectChannels({ keepEmpty: false });
  collectMergeRules({ keepEmpty: false });
}

async function saveConfig({ quiet = false } = {}) {
  collectForms();
  const result = await api("/api/config", { method: "POST", body: { config: state.config } });
  state.config = result.config;
  const newToken = state.config?.web_config?.token;
  if (newToken) {
    state.token = newToken;
    localStorage.setItem("telegram_forwarder_token", state.token);
  }
  renderAll();
  if (!quiet) {
    showToast(result.web_restart_required ? "配置已保存，Web host/port 需重载插件生效。" : "配置已保存。");
  }
}

async function saveRawConfig() {
  let parsed;
  try {
    parsed = JSON.parse(els.rawConfigInput.value);
  } catch (error) {
    showToast(`JSON 格式错误：${error.message}`);
    return;
  }
  const result = await api("/api/config", { method: "POST", body: { config: parsed } });
  state.config = result.config;
  const newToken = state.config?.web_config?.token;
  if (newToken) {
    state.token = newToken;
    localStorage.setItem("telegram_forwarder_token", state.token);
  }
  renderAll();
  showToast(result.web_restart_required ? "JSON 配置已保存，Web host/port 需重载插件生效。" : "JSON 配置已保存。");
}

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function parseTransferJson(input, label) {
  try {
    return JSON.parse(input.value);
  } catch (error) {
    throw new Error(`${label} JSON 格式错误：${error.message}`);
  }
}

async function exportConfig() {
  const data = await api("/api/export/config");
  els.rawConfigInput.value = JSON.stringify(data.config || data, null, 2);
  downloadJson("telegram-forwarder-config.json", data);
  return { message: "配置已导出。" };
}

async function importConfigPayload(payload) {
  const result = await api("/api/import/config", { method: "POST", body: payload });
  state.config = result.config || state.config;
  await loadAll();
  return result;
}

function readJsonFile(file, label) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      try {
        resolve(JSON.parse(String(reader.result || "")));
      } catch (error) {
        reject(new Error(`${label} JSON 格式错误：${error.message}`));
      }
    });
    reader.addEventListener("error", () => reject(new Error(`${label} 文件读取失败。`)));
    reader.readAsText(file, "utf-8");
  });
}

async function importConfigFromFile(file) {
  const payload = await readJsonFile(file, "配置");
  return importConfigPayload(payload);
}

async function exportSession() {
  const data = await api("/api/export/session");
  downloadJson("telegram-forwarder-session.json", data);
  return { message: "登录信息已导出。" };
}

async function importSessionFromFile(file) {
  const payload = await readJsonFile(file, "登录信息");
  const result = await api("/api/import/session", { method: "POST", body: payload });
  await loadAll();
  return result;
}

async function loadAll() {
  const [status, configData] = await Promise.all([api("/api/status"), api("/api/config")]);
  state.status = status;
  state.config = configData.config;
  renderAll();
}

function setSection(section) {
  state.section = section;
  document.querySelectorAll(".section").forEach((node) => node.classList.toggle("active", node.id === `section-${section}`));
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.section === section));
  const active = document.querySelector(`.nav-item[data-section="${section}"]`);
  els.sectionTitle.textContent = active?.textContent || "总览";
  updateTopbarActions();
  closeSidebar();
}

function updateTopbarActions() {
  const configSections = new Set(["targets", "channels", "merge", "global", "web"]);
  const refreshOnlySections = new Set(["overview"]);
  const showRefresh = configSections.has(state.section) || refreshOnlySections.has(state.section);
  const showSave = configSections.has(state.section);
  els.refreshBtn.hidden = !showRefresh;
  els.saveBtn.hidden = !showSave;
  els.refreshBtn.parentElement.hidden = !showRefresh && !showSave;
}

function openSidebar() {
  document.body.classList.add("sidebar-open");
  els.sidebar.classList.add("open");
  els.sidebarScrim.classList.add("show");
  els.mobileMenu.classList.add("hidden");
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
  els.sidebar.classList.remove("open");
  els.sidebarScrim.classList.remove("show");
  els.mobileMenu.classList.remove("hidden");
}

async function enterApp() {
  els.authScreen.hidden = true;
  els.appShell.hidden = false;
  await loadAll();
}

async function loginWithToken(event) {
  event.preventDefault();
  els.authError.textContent = "";
  const token = els.tokenInput.value.trim();
  if (!token) {
    els.authError.textContent = "请输入 Web Token。";
    return;
  }
  try {
    if (!(await checkToken(token))) {
      els.authError.textContent = "Token 不正确。";
      return;
    }
    state.token = token;
    localStorage.setItem("telegram_forwarder_token", token);
    await enterApp();
  } catch (error) {
    els.authError.textContent = error.message;
  }
}

async function withAction(action, doneMessage) {
  try {
    const result = await action();
    await loadAll();
    showToast(result?.message || doneMessage);
  } catch (error) {
    showToast(error.message);
  }
}

async function withButtonLoading(button, label, action, doneMessage) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await withAction(action, doneMessage);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

function bindEvents() {
  els.authForm.addEventListener("submit", loginWithToken);
  els.logoutBtn.addEventListener("click", () => {
    localStorage.removeItem("telegram_forwarder_token");
    window.location.reload();
  });
  els.refreshBtn.addEventListener("click", () => withAction(loadAll, "已刷新。"));
  els.saveBtn.addEventListener("click", () => withAction(() => saveConfig(), "配置已保存。"));
  els.saveRawBtn.addEventListener("click", () => withAction(saveRawConfig, "JSON 配置已保存。"));

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setSection(button.dataset.section));
  });
  els.mobileMenu.addEventListener("click", openSidebar);
  els.sidebarScrim.addEventListener("click", closeSidebar);

  els.addChannelBtn.addEventListener("click", () => {
    collectChannels();
    const channel = defaultChannel();
    state.config.source_channels.push(channel);
    state.expandedChannels.add(channelKey(channel, state.config.source_channels.length - 1));
    renderChannels();
  });
  els.addMergeRuleBtn.addEventListener("click", () => {
    collectMergeRules();
    if (!Array.isArray(state.config.merge_rules)) {
      state.config.merge_rules = [];
    }
    const rule = defaultMergeRule();
    state.config.merge_rules.push(rule);
    state.expandedMergeRules.add(mergeRuleKey(rule, state.config.merge_rules.length - 1));
    renderMergeRules();
  });

  els.sendCodeBtn.addEventListener("click", () =>
    withButtonLoading(els.sendCodeBtn, "正在发送验证码...", async () => {
      await saveConfig({ quiet: true });
      const result = await api("/api/login/start", {
        method: "POST",
        body: {
          phone: els.phoneInput.value.trim(),
          replace_existing: Boolean(state.status?.telegram?.replace_existing),
        },
      });
      els.loginMessage.textContent = result.message || "";
      return result;
    }, "验证码已发送。"),
  );
  els.submitCodeBtn.addEventListener("click", () =>
    withButtonLoading(els.submitCodeBtn, "正在验证验证码...", async () => {
      const result = await api("/api/login/code", { method: "POST", body: { code: els.codeInput.value.trim() } });
      els.loginMessage.textContent = result.message || "";
      return result;
    }, "验证码已提交。"),
  );
  els.submitPasswordBtn.addEventListener("click", () =>
    withButtonLoading(els.submitPasswordBtn, "正在登录...", async () => {
      const result = await api("/api/login/password", { method: "POST", body: { password: els.passwordInput.value } });
      els.loginMessage.textContent = result.message || "";
      return result;
    }, "密码已提交。"),
  );
  els.resetLoginBtn.addEventListener("click", () => {
    withButtonLoading(els.resetLoginBtn, "正在准备...", () => api("/api/login/reset", { method: "POST" }), "已进入重新登录流程。");
  });

  els.exportConfigBtn.addEventListener("click", () => withAction(exportConfig, "配置已导出。"));
  els.importConfigBtn.addEventListener("click", () => {
    els.configImportFile.value = "";
    els.configImportFile.click();
  });
  els.configImportFile.addEventListener("change", () => {
    const file = els.configImportFile.files?.[0];
    if (!file) return;
    withAction(() => importConfigFromFile(file), "配置已导入。");
  });
  els.exportSessionBtn.addEventListener("click", () =>
    withButtonLoading(els.exportSessionBtn, "正在导出...", exportSession, "登录信息已导出。"),
  );
  els.importSessionBtn.addEventListener("click", () => {
    els.sessionImportFile.value = "";
    els.sessionImportFile.click();
  });
  els.sessionImportFile.addEventListener("change", () => {
    const file = els.sessionImportFile.files?.[0];
    if (!file) return;
    withButtonLoading(els.importSessionBtn, "正在导入...", () => importSessionFromFile(file), "登录信息已导入。");
  });

  els.runCheckBtn.addEventListener("click", () => withAction(() => api("/api/runtime/check", { method: "POST" }), "已触发。"));
  els.pauseBtn.addEventListener("click", () => withAction(() => api("/api/runtime/pause", { method: "POST" }), "已暂停。"));
  els.resumeBtn.addEventListener("click", () => withAction(() => api("/api/runtime/resume", { method: "POST" }), "已恢复。"));
  els.clearQueueBtn.addEventListener("click", () => {
    if (!window.confirm("确认清空全部待发送队列？")) return;
    withAction(() => api("/api/runtime/clear-queue", { method: "POST", body: { target: "all" } }), "队列已清空。");
  });
}

async function boot() {
  cacheElements();
  bindEvents();
  els.tokenInput.value = state.token || "";
  if (!state.token) return;
  try {
    if (await checkToken(state.token)) {
      await enterApp();
    } else {
      localStorage.removeItem("telegram_forwarder_token");
      state.token = "";
    }
  } catch (error) {
    console.warn("Token validation failed:", error);
    localStorage.removeItem("telegram_forwarder_token");
    state.token = "";
  }
}

document.addEventListener("DOMContentLoaded", boot);
