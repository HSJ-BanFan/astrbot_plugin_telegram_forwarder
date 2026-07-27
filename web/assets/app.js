import { store } from './js/store.js';
import { apiRequest, isDashboardPage } from './js/api.js';
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
  setRenderAllCallback,
  bindCardPhysics,
  renderDashboardLoadError
} from './js/context.js';
import { initLogin, checkToken } from './js/ui_login.js';
import { initOverview, renderStatus } from './js/ui_overview.js';
import { initChannels, collectChannels, collectMergeRules, renderChannels, renderMergeRules } from './js/ui_channels.js';
import { renderQQTargetSelector, renderTGChannelSelector } from './js/ui_selector.js';
import { renderSidebarStatusCard, renderRootConfig, renderForwardTabs, renderForwardConfig, renderRawConfig, collectRootConfig, collectForwardConfig } from './js/ui_config.js';
import { animateTopologyInto, renderTargetTopology, renderTopologySurfaces, syncTopologyControls, topologyUiState, TOPOLOGY_FILTERS, setTopologySectionHandler } from './js/ui_topology.js';
import { motionEnabled, safeStorageRemove, safeStorageSet } from './js/utils.js';

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
    "sidebarFooter",
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
    "overviewTopology",
    "topologySearchInput",
    "topologyFilterGroup",
    "loginBadge",
    "loginMessage",
    "loginAccountCard",
    "loginAccountInfo",
    "apiIdInput",
    "apiHashInput",
    "phoneInput",
    "proxyProtocolInput",
    "proxyHostInput",
    "proxyPortInput",
    "proxyUsernameInput",
    "proxyPasswordInput",
    "codeInput",
    "passwordInput",
    "sendCodeBtn",
    "submitCodeBtn",
    "submitPasswordBtn",
    "resetLoginBtn",
    "targetChannelSelector",
    "targetChannelInput",
    "defaultQQSelector",
    "targetQQInput",
    "resetTargetChannelBtn",
    "targetTopology",
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
  renderSidebarStatusCard();
}

async function saveRawConfig() {
  if (!els.rawConfigInput) return;
  let parsed;
  try {
    parsed = JSON.parse(els.rawConfigInput.value);
  } catch (error) {
    showToast(`JSON 格式错误：${error.message}`);
    throw error;
  }
  const result = await apiRequest("/api/config", "POST", { config: parsed });
  store.updateState({ config: result.config });
  const newToken = result.config?.web_config?.token;
  if (newToken) {
    store.updateState({ token: newToken });
    safeStorageSet("telegram_forwarder_token", newToken);
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
    if (isTarget && motionEnabled()) {
      // GSAP 接管入场时关闭 CSS 兜底动画，避免透明度叠乘造成的闪烁
      node.style.animation = "none";
      const cards = node.querySelectorAll(":scope .metric-card, :scope .panel");
      window.gsap.killTweensOf([node, ...cards]);
      window.gsap.fromTo(
        node,
        { opacity: 0, y: 10 },
        { opacity: 1, y: 0, duration: 0.3, ease: "power2.out", clearProps: "opacity,transform" }
      );
      if (cards.length) {
        window.gsap.fromTo(
          cards,
          { opacity: 0, y: 16 },
          { opacity: 1, y: 0, duration: 0.36, ease: "power2.out", stagger: 0.055, delay: 0.05, clearProps: "opacity,transform" }
        );
      }
    }
  });
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.section === section));
  const active = document.querySelector(`.nav-item[data-section="${section}"]`);
  if (els.sectionTitle) {
    els.sectionTitle.textContent =
      active?.querySelector(".nav-label")?.textContent?.trim() || active?.textContent?.trim() || "总览";
  }

  // Trigger GSAP entrance animations for the active section's topology
  if (section === "targets" && els.targetTopology) {
    animateTopologyInto(els.targetTopology);
  } else if (section === "overview" && els.overviewTopology) {
    animateTopologyInto(els.overviewTopology);
  }

  updateTopbarActions();
  closeSidebar();
}

setTopologySectionHandler(setSection);

export function updateTopbarActions() {
  const configSections = new Set(["targets", "channels", "merge", "global", "web"]);
  const refreshOnlySections = new Set(["overview"]);
  const showRefresh = configSections.has(store.state.section) || refreshOnlySections.has(store.state.section);
  const showSave = configSections.has(store.state.section);
  if (els.refreshBtn) els.refreshBtn.hidden = !showRefresh;
  if (els.saveBtn) els.saveBtn.hidden = !showSave;
  if (els.refreshBtn?.parentElement) {
    const parent = els.refreshBtn.parentElement;
    parent.hidden = !showRefresh && !showSave;
    parent.dataset.actions = showRefresh && showSave ? "both" : showRefresh ? "refresh" : showSave ? "save" : "none";
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
  if (els.resetTargetChannelBtn) {
    els.resetTargetChannelBtn.addEventListener("click", () => {
      collectRootConfig();
      if (els.targetChannelInput) els.targetChannelInput.value = "";
      store.state.config.target_channel = "";
      renderTGChannelSelector({
        root: els.targetChannelSelector,
        manualInput: els.targetChannelInput,
        compact: true,
      });
      showToast("Telegram 目标已清空，保存后生效。");
    });
  }
  if (els.targetQQInput) {
    els.targetQQInput.addEventListener("change", () => {
      collectRootConfig();
      renderTopologySurfaces();
    });
  }
  if (els.targetChannelInput) {
    els.targetChannelInput.addEventListener("change", () => {
      collectRootConfig();
      renderTGChannelSelector({
        root: els.targetChannelSelector,
        manualInput: els.targetChannelInput,
        compact: true,
      });
    });
  }
  if (els.topologySearchInput) {
    els.topologySearchInput.addEventListener("input", () => {
      topologyUiState.query = els.topologySearchInput.value.trim();
      renderTargetTopology();
    });
  }
  els.topologyFilterGroup?.querySelectorAll("[data-topology-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextFilter = button.dataset.topologyFilter || "all";
      topologyUiState.filter = TOPOLOGY_FILTERS.has(nextFilter) ? nextFilter : "all";
      syncTopologyControls();
      renderTargetTopology();
    });
  });

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

  let lastRenderedConfig = null;

  // Register main router to subscribe to store updates
  store.subscribe((state) => {
    // Selector and topology rendering is expensive; status polling must not trigger it.
    if (state.config && state.config !== lastRenderedConfig && els.defaultQQSelector && els.targetQQInput) {
      lastRenderedConfig = state.config;
      renderTGChannelSelector({
        root: els.targetChannelSelector,
        manualInput: els.targetChannelInput,
        compact: true,
      });
      renderQQTargetSelector({
        root: els.defaultQQSelector,
        manualInput: els.targetQQInput,
        inheritLabel: "未配置默认 QQ 目标",
        compact: true,
      });
      renderTopologySurfaces();
      renderSidebarStatusCard();
    }
  });

  // Init UI modules
  initLogin();
  initOverview();
  initChannels();

  // Bind events for entrypoint page
  bindMainEvents();
  syncTopologyControls();
  document.querySelectorAll(".metric-card, .overview-main > .panel").forEach(bindCardPhysics);

  if (isDashboardPage()) {
    store.updateState({ token: "dashboard" });
    try {
      await enterApp();
    } catch (error) {
      console.error("Dashboard Page boot failed:", error);
      renderDashboardLoadError(error);
    }
    return;
  }

  if (els.tokenInput) els.tokenInput.value = store.state.token || "";
  if (!store.state.token) return;
  try {
    if (await checkToken(store.state.token)) {
      await enterApp();
    } else {
      safeStorageRemove("telegram_forwarder_token");
      store.updateState({ token: "" });
    }
  } catch (error) {
    console.warn("Token validation failed:", error);
    safeStorageRemove("telegram_forwarder_token");
    store.updateState({ token: "" });
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}
