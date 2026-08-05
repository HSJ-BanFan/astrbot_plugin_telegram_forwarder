import { store } from './store.js';
import { safeStorageRemove } from './utils.js';

const LEGACY_API_PREFIX = "/api/";

function dashboardBridge() {
  return window.AstrBotPluginPage || null;
}

export function isDashboardPage() {
  return Boolean(dashboardBridge());
}

function bridgeEndpoint(path) {
  const value = String(path || "").trim();
  if (value.startsWith(LEGACY_API_PREFIX)) return value.slice(LEGACY_API_PREFIX.length);
  return value.replace(/^\/+/, "");
}

async function withTimeout(promise, timeout) {
  if (!timeout || timeout <= 0) return promise;
  let timeoutId;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timeoutId = window.setTimeout(() => reject(new Error("请求超时，请稍后重试。")), timeout);
      }),
    ]);
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function bridgeRequest(path, method = "GET", body = null, timeout = 30000) {
  return withTimeout((async () => {
    const bridge = dashboardBridge();
    if (!bridge) throw new Error("AstrBot Dashboard bridge is not available.");
    await bridge.ready();
    const endpoint = bridgeEndpoint(path);
    if (method.toUpperCase() === "GET") {
      return bridge.apiGet(endpoint, body);
    }
    return bridge.apiPost(endpoint, body || {});
  })(), timeout);
}

export async function apiRequest(path, method = 'GET', body = null, timeout = 30000) {
  if (isDashboardPage()) {
    return bridgeRequest(path, method, body, timeout);
  }

  const headers = {
    'Content-Type': 'application/json',
    'X-Admin-Token': store.state.token
  };
  const options = { method, headers };
  if (body) options.body = JSON.stringify(body);

  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeout);
  options.signal = controller.signal;

  try {
    const res = await fetch(path, options);
    clearTimeout(id);
    if (res.status === 401 || res.status === 410) {
      store.updateState({ token: "" });
      safeStorageRemove("telegram_forwarder_token");
      throw new Error("登录已过期，请重新输入 Token");
    }
    const payload = await res.json();
    if (!res.ok || payload?.ok === false) {
      throw new Error(payload?.message || "请求失败");
    }
    const data = payload?.data ?? {};
    if (data && typeof data === "object" && !Array.isArray(data) && payload?.message && !data.message) {
      data.message = payload.message;
    }
    return data;
  } catch (err) {
    clearTimeout(id);
    throw err;
  }
}
