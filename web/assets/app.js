import { store } from './js/store.js';
import { apiRequest } from './js/api.js';
import {
  els,
  showToast,
  saveConfig,
  loadAll,
  loadStatusOnly,
  enterApp,
  withAction,
  withButtonLoading,
  setCollectFormsCallback,
  setRenderAllCallback
} from './js/context.js';
import { initLogin, checkToken } from './js/ui_login.js';
import { initOverview, renderStatus } from './js/ui_overview.js';
import { initChannels, collectChannels, collectMergeRules, renderChannels, renderMergeRules } from './js/ui_channels.js';
import { renderQQTargetSelector, splitList, joinList } from './js/ui_selector.js';
import { escapeHtml } from './js/utils.js';

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
const DEFAULT_WEB_CONFIG = { enabled: true, host: "127.0.0.1", port: 8180, token: "" };

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
    "runtimeState",
    "runtimeLog",
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
    "defaultQQSelector",
    "targetQQInput",
    "resetDefaultQQBtn",
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

export function intValue(id, fallback = 0) {
  const parsed = Number.parseInt(els[id]?.value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function currentWebConfig() {
  return { ...DEFAULT_WEB_CONFIG, ...(store.state.config?.web_config || {}) };
}

export function renderRootConfig() {
  const cfg = store.state.config || {};
  if (els.apiIdInput) els.apiIdInput.value = cfg.api_id || "";
  if (els.apiHashInput) els.apiHashInput.value = cfg.api_hash || "";
  if (els.phoneInput) els.phoneInput.value = cfg.phone || "";
  if (els.proxyInput) els.proxyInput.value = cfg.proxy || "";
  if (els.targetChannelInput) els.targetChannelInput.value = cfg.target_channel || "";
  if (els.targetQQInput) els.targetQQInput.value = joinList(cfg.target_qq_session || []);
  
  renderQQTargetSelector({
    root: els.defaultQQSelector,
    manualInput: els.targetQQInput,
    inheritLabel: "未配置默认 QQ 目标",
  });
  if (els.debugDefaultInput) els.debugDefaultInput.checked = Boolean(cfg.debug_enabled_default);

  const web = currentWebConfig();
  if (els.webEnabledInput) els.webEnabledInput.checked = Boolean(web.enabled);
  if (els.webHostInput) els.webHostInput.value = web.host;
  if (els.webPortInput) els.webPortInput.value = web.port;
  if (els.webTokenInput) els.webTokenInput.value = web.token;
}

export function renderForwardTabs() {
  if (!els.forwardTabs) return;
  els.forwardTabs.innerHTML = FORWARD_GROUPS.map(
    (group) =>
      `<button type="button" data-forward-tab="${group.id}" class="${store.state.forwardGroup === group.id ? "active" : ""}">${escapeHtml(group.label)}</button>`,
  ).join("");
  document.querySelectorAll("[data-forward-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      collectForwardConfig();
      store.updateState({ forwardGroup: button.dataset.forwardTab });
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

export function renderForwardConfig() {
  if (!els.forwardConfigForm) return;
  const cfg = store.state.config?.forward_config || {};
  const group = FORWARD_GROUPS.find((item) => item.id === store.state.forwardGroup) || FORWARD_GROUPS[0];
  els.forwardConfigForm.innerHTML = group.fields
    .map((field) => renderField(field, cfg[field.key] ?? field.defaultValue, "data-forward"))
    .join("");
}

export function renderRawConfig() {
  if (els.rawConfigInput) {
    els.rawConfigInput.value = JSON.stringify(store.state.config || {}, null, 2);
  }
}

export function collectRootConfig() {
  if (!store.state.config) store.state.config = {};
  store.state.config.api_id = intValue("apiIdInput", 0);
  store.state.config.api_hash = els.apiHashInput?.value.trim() || "";
  store.state.config.phone = els.phoneInput?.value.trim() || "";
  store.state.config.proxy = els.proxyInput?.value.trim() || "";
  store.state.config.target_channel = els.targetChannelInput?.value.trim() || "";
  store.state.config.target_qq_session = splitList(els.targetQQInput?.value || "");
  store.state.config.debug_enabled_default = els.debugDefaultInput ? els.debugDefaultInput.checked : false;
  store.state.config.web_config = {
    enabled: els.webEnabledInput ? els.webEnabledInput.checked : true,
    host: els.webHostInput?.value.trim() || DEFAULT_WEB_CONFIG.host,
    port: intValue("webPortInput", DEFAULT_WEB_CONFIG.port),
    token: els.webTokenInput?.value.trim() || "",
  };
}

export function collectForwardConfig() {
  if (!store.state.config) store.state.config = {};
  const cfg = { ...(store.state.config.forward_config || {}) };
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
  store.state.config.forward_config = cfg;
}

export function collectForms() {
  collectRootConfig();
  collectForwardConfig();
  collectChannels({ keepEmpty: false });
  collectMergeRules({ keepEmpty: false });
}

export function renderAll() {
  renderStatus();
  renderRootConfig();
  renderForwardTabs();
  renderForwardConfig();
  renderChannels();
  renderMergeRules();
  renderRawConfig();
  updateTopbarActions();
}

async function saveRawConfig() {
  if (!els.rawConfigInput) return;
  let parsed;
  try {
    parsed = JSON.parse(els.rawConfigInput.value);
  } catch (error) {
    showToast(`JSON 格式错误：${error.message}`);
    return;
  }
  const result = await apiRequest("/api/config", "POST", { config: parsed });
  store.updateState({ config: result.config });
  const newToken = result.config?.web_config?.token;
  if (newToken) {
    store.updateState({ token: newToken });
    localStorage.setItem("telegram_forwarder_token", newToken);
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

async function exportConfig() {
  const data = await apiRequest("/api/export/config");
  if (els.rawConfigInput) {
    els.rawConfigInput.value = JSON.stringify(data.config || data, null, 2);
  }
  downloadJson("telegram-forwarder-config.json", data);
  return { message: "配置已导出。" };
}

async function importConfigPayload(payload) {
  const result = await apiRequest("/api/import/config", "POST", payload);
  store.updateState({ config: result.config || store.state.config });
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

export function setSection(section) {
  store.updateState({ section });
  document.querySelectorAll(".section").forEach((node) => {
    const isTarget = node.id === `section-${section}`;
    node.classList.toggle("active", isTarget);
    if (isTarget && window.gsap) {
      window.gsap.fromTo(node,
        { opacity: 0, y: 15 },
        { opacity: 1, y: 0, duration: 0.35, ease: "power2.out" }
      );
    }
  });
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.section === section));
  const active = document.querySelector(`.nav-item[data-section="${section}"]`);
  if (els.sectionTitle) {
    els.sectionTitle.textContent = active?.textContent || "总览";
  }
  updateTopbarActions();
  closeSidebar();
}

export function updateTopbarActions() {
  const configSections = new Set(["targets", "channels", "merge", "global", "web"]);
  const refreshOnlySections = new Set(["overview"]);
  const showRefresh = configSections.has(store.state.section) || refreshOnlySections.has(store.state.section);
  const showSave = configSections.has(store.state.section);
  if (els.refreshBtn) els.refreshBtn.hidden = !showRefresh;
  if (els.saveBtn) els.saveBtn.hidden = !showSave;
  if (els.refreshBtn?.parentElement) {
    els.refreshBtn.parentElement.hidden = !showRefresh && !showSave;
  }
}

export function openSidebar() {
  document.body.classList.add("sidebar-open");
  if (els.sidebar) els.sidebar.classList.add("open");
  if (els.sidebarScrim) els.sidebarScrim.classList.add("show");
  if (els.mobileMenu) els.mobileMenu.classList.add("hidden");
}

export function closeSidebar() {
  document.body.classList.remove("sidebar-open");
  if (els.sidebar) els.sidebar.classList.remove("open");
  if (els.sidebarScrim) els.sidebarScrim.classList.remove("show");
  if (els.mobileMenu) els.mobileMenu.classList.remove("hidden");
}

function bindMainEvents() {
  if (els.refreshBtn) {
    els.refreshBtn.addEventListener("click", () => withAction(loadAll, "已刷新。", { refresh: false }));
  }
  if (els.saveBtn) {
    els.saveBtn.addEventListener("click", () => withAction(() => saveConfig(), "配置已保存。"));
  }
  if (els.saveRawBtn) {
    els.saveRawBtn.addEventListener("click", () => withAction(saveRawConfig, "JSON 配置已保存。"));
  }
  if (els.resetDefaultQQBtn) {
    els.resetDefaultQQBtn.addEventListener("click", () => {
      collectRootConfig();
      if (els.targetQQInput) els.targetQQInput.value = "";
      store.state.config.target_qq_session = [];
      renderRootConfig();
      showToast("默认 QQ 目标已清空，保存后生效。");
    });
  }

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setSection(button.dataset.section));
  });
  
  if (els.mobileMenu) {
    els.mobileMenu.addEventListener("click", openSidebar);
  }
  if (els.sidebarScrim) {
    els.sidebarScrim.addEventListener("click", closeSidebar);
  }

  if (els.exportConfigBtn) {
    els.exportConfigBtn.addEventListener("click", () => withAction(exportConfig, "配置已导出。"));
  }
  if (els.importConfigBtn && els.configImportFile) {
    els.importConfigBtn.addEventListener("click", () => {
      els.configImportFile.value = "";
      els.configImportFile.click();
    });
    els.configImportFile.addEventListener("change", () => {
      const file = els.configImportFile.files?.[0];
      if (!file) return;
      withAction(() => importConfigFromFile(file), "配置已导入。");
    });
  }
}

async function boot() {
  cacheElements();
  setCollectFormsCallback(collectForms);
  setRenderAllCallback(renderAll);
  
  // Register main router to subscribe to store updates
  store.subscribe((state) => {
    // Whenever status or config updates in store, re-run selectors rendering
    if (state.config && els.defaultQQSelector && els.targetQQInput) {
      renderQQTargetSelector({
        root: els.defaultQQSelector,
        manualInput: els.targetQQInput,
        inheritLabel: "未配置默认 QQ 目标",
      });
    }
  });

  // Init UI modules
  initLogin();
  initOverview();
  initChannels();
  
  // Bind events for entrypoint page
  bindMainEvents();

  if (els.tokenInput) els.tokenInput.value = store.state.token || "";
  if (!store.state.token) return;
  try {
    if (await checkToken(store.state.token)) {
      await enterApp();
    } else {
      localStorage.removeItem("telegram_forwarder_token");
      store.updateState({ token: "" });
    }
  } catch (error) {
    console.warn("Token validation failed:", error);
    localStorage.removeItem("telegram_forwarder_token");
    store.updateState({ token: "" });
  }
}

document.addEventListener("DOMContentLoaded", boot);
