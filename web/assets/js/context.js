import { store } from './store.js';
import { apiRequest, isDashboardPage } from './api.js';
import { safeStorageSet } from './utils.js';

export const els = {};

export function showToast(message) {
  if (els.toast) {
    els.toast.textContent = message;
    els.toast.classList.add("show");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => els.toast.classList.remove("show"), 2800);
  }
}

export function renderDashboardLoadError(error) {
  const message = error?.message || "未知错误";
  const host = els.appShell?.querySelector(".content") || document.body;
  let panel = document.querySelector("[data-dashboard-load-error]");
  if (!panel) {
    panel = document.createElement("div");
    panel.className = "dashboard-load-error";
    panel.setAttribute("data-dashboard-load-error", "true");
    const topbar = host.querySelector?.(".topbar");
    if (topbar?.parentNode) {
      topbar.insertAdjacentElement("afterend", panel);
    } else {
      host.prepend(panel);
    }
  }
  panel.textContent = "";
  const title = document.createElement("strong");
  title.textContent = "Dashboard 插件页加载失败";
  const hint = document.createElement("span");
  hint.textContent = "请在 AstrBot Dashboard 控制台查看该插件页的网络请求和 Console 错误。";
  const detail = document.createElement("code");
  detail.textContent = message;
  panel.append(title, hint, detail);
  showToast(`Dashboard 插件页加载失败：${message}`);
}

function applyDashboardPayload(dashboardPayload) {
  const qqGroups = dashboardPayload.qqGroups || {};
  const tgChannels = dashboardPayload.tgChannels || {};
  store.updateState({
    status: dashboardPayload.status || {},
    config: dashboardPayload.config || {},
    qqGroups: Array.isArray(qqGroups.groups) ? qqGroups.groups : [],
    qqGroupsAvailable: Boolean(qqGroups.available),
    qqGroupsMessage: qqGroups.message || "",
    tgChannels: Array.isArray(tgChannels.channels) ? tgChannels.channels : [],
    tgChannelsAvailable: Boolean(tgChannels.available),
    tgChannelsMessage: tgChannels.message || "",
  });
  const errors = dashboardPayload.errors || {};
  const failedSections = Object.keys(errors);
  if (failedSections.length) {
    showToast(`部分 Dashboard 数据加载失败：${failedSections.join(", ")}`);
  }
}

export async function loadQQGroups({ force = false } = {}) {
  try {
    const data = await apiRequest(force ? "/api/qq/groups/refresh" : "/api/qq/groups", force ? "POST" : "GET");
    store.updateState({
      qqGroups: Array.isArray(data.groups) ? data.groups : [],
      qqGroupsAvailable: Boolean(data.available),
      qqGroupsMessage: data.message || "",
    });
    return data;
  } catch (error) {
    store.updateState({
      qqGroups: [],
      qqGroupsAvailable: false,
      qqGroupsMessage: error.message,
    });
    return { groups: [], available: false, message: error.message };
  }
}

export async function loadTGChannels({ force = false } = {}) {
  try {
    const data = await apiRequest(force ? "/api/tg/channels/refresh" : "/api/tg/channels", force ? "POST" : "GET");
    store.updateState({
      tgChannels: Array.isArray(data.channels) ? data.channels : [],
      tgChannelsAvailable: Boolean(data.available),
      tgChannelsMessage: data.message || "",
    });
    return data;
  } catch (error) {
    store.updateState({
      tgChannels: [],
      tgChannelsAvailable: false,
      tgChannelsMessage: error.message,
    });
    return { channels: [], available: false, message: error.message };
  }
}

export async function loadAll() {
  if (isDashboardPage()) {
    const dashboardPayload = await apiRequest("/api/dashboard");
    applyDashboardPayload(dashboardPayload);
    syncRuntimeStatusRefresh();
    if (renderAllCallback) renderAllCallback();
    return;
  }

  const [status, configData] = await Promise.all([
    apiRequest("/api/status"),
    apiRequest("/api/config"),
    loadQQGroups(),
    loadTGChannels(),
  ]);
  store.updateState({
    status: status,
    config: configData.config,
  });
  syncRuntimeStatusRefresh();
  if (renderAllCallback) renderAllCallback();
}

export async function loadStatusOnly() {
  if (store.state.runtimeRefreshInFlight) return;
  store.updateState({ runtimeRefreshInFlight: true });
  try {
    const status = await apiRequest("/api/status");
    store.updateState({ status });
  } finally {
    store.updateState({ runtimeRefreshInFlight: false });
    syncRuntimeStatusRefresh();
  }
}

export function runtimeNeedsStatusRefresh() {
  const runtime = store.state.status?.runtime || {};
  const operations = Array.isArray(runtime.operations) ? runtime.operations : [];
  return Boolean(
    runtime.active_web_operations ||
      runtime.capture_busy ||
      runtime.send_busy ||
      runtime.global_send_busy ||
      operations.some((operation) => operation.status === "running")
  );
}

export function syncRuntimeStatusRefresh() {
  if (!runtimeNeedsStatusRefresh()) {
    if (store.state.runtimeRefreshTimer) {
      window.clearTimeout(store.state.runtimeRefreshTimer);
      store.updateState({ runtimeRefreshTimer: null });
    }
    return;
  }
  if (store.state.runtimeRefreshTimer || !store.state.token || els.appShell.hidden) return;
  const timer = window.setTimeout(async () => {
    store.updateState({ runtimeRefreshTimer: null });
    if (!store.state.token || els.appShell.hidden) return;
    try {
      await loadStatusOnly();
    } catch (error) {
      console.warn("Runtime status refresh failed:", error);
    }
  }, 2000);
  store.updateState({ runtimeRefreshTimer: timer });
}

let collectFormsCallback = null;
export function setCollectFormsCallback(cb) {
  collectFormsCallback = cb;
}

let renderAllCallback = null;
export function setRenderAllCallback(cb) {
  renderAllCallback = cb;
}

export async function saveConfig({ quiet = false } = {}) {
  if (collectFormsCallback) collectFormsCallback();
  const result = await apiRequest("/api/config", "POST", { config: store.state.config });
  store.updateState({ config: result.config });
  const newToken = result.config?.web_config?.token;
  if (newToken) {
    store.updateState({ token: newToken });
    safeStorageSet("telegram_forwarder_token", newToken);
  }
  if (!quiet) {
    showToast(result.web_restart_required ? "配置已保存，Web host/port 需重载插件生效。" : "配置已保存。");
  }
}

export async function withAction(action, doneMessage, options = {}) {
  try {
    const result = await action();
    const refresh = options.refresh ?? "all";
    if (refresh === "status") {
      await loadStatusOnly();
    } else if (refresh !== false && refresh !== "none") {
      await loadAll();
    }
    showToast(result?.message || doneMessage);
  } catch (error) {
    showToast(error.message);
  }
}

export async function withButtonLoading(button, label, action, doneMessage) {
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

export async function enterApp() {
  if (els.authScreen) els.authScreen.hidden = true;
  if (els.appShell) els.appShell.hidden = false;
  await loadAll();
}

export function bindCardPhysics(el) {
  if (!el || !window.gsap) return;
  if (el.dataset.physicsBound === "true") return;
  el.dataset.physicsBound = "true";
  
  el.addEventListener("mouseenter", () => {
    window.gsap.to(el, {
      y: -4,
      boxShadow: "0 22px 48px rgba(45, 118, 166, 0.16)",
      duration: 0.3,
      ease: "power2.out"
    });
  });

  el.addEventListener("mouseleave", () => {
    window.gsap.to(el, {
      y: 0,
      boxShadow: "var(--shadow)",
      scale: 1,
      duration: 0.3,
      ease: "power2.out"
    });
  });

  el.addEventListener("mousedown", () => {
    window.gsap.to(el, {
      scale: 0.98,
      duration: 0.1,
      ease: "power1.out"
    });
  });

  el.addEventListener("mouseup", () => {
    window.gsap.to(el, {
      scale: 1,
      duration: 0.25,
      ease: "back.out(2)"
    });
  });
}
