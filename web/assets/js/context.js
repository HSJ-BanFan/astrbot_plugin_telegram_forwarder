import { store } from './store.js';
import { apiRequest } from './api.js';

export const els = {};

export function showToast(message) {
  if (els.toast) {
    els.toast.textContent = message;
    els.toast.classList.add("show");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => els.toast.classList.remove("show"), 2800);
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

export async function saveConfig({ quiet = false } = {}) {
  if (collectFormsCallback) collectFormsCallback();
  const result = await apiRequest("/api/config", "POST", { config: store.state.config });
  store.updateState({ config: result.config });
  const newToken = result.config?.web_config?.token;
  if (newToken) {
    store.updateState({ token: newToken });
    localStorage.setItem("telegram_forwarder_token", newToken);
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
