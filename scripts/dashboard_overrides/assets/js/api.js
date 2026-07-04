import { store } from './store.js';
import { safeStorageRemove } from './utils.js';

const LEGACY_API_PREFIX = "/api/";

function dashboardBridge() {
  return window.AstrBotPluginPage || null;
}

export function isDashboardPage() {
  return Boolean(dashboardBridge()) || window.location.pathname.startsWith("/api/plugin/page/content/");
}

async function waitForDashboardBridge(timeout = 5000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    const bridge = dashboardBridge();
    if (bridge) return bridge;
    await new Promise((resolve) => window.setTimeout(resolve, 25));
  }
  throw new Error("AstrBot Dashboard bridge is not available.");
}

function bridgeEndpoint(path) {
  const value = String(path || "").trim();
  if (value.startsWith(LEGACY_API_PREFIX)) {
    const legacyEndpoint = value.slice(LEGACY_API_PREFIX.length);
    return `page/${legacyEndpoint}`;
  }
  const endpoint = value.replace(/^\/+/, "");
  return endpoint.startsWith("page/") ? endpoint : `page/${endpoint}`;
}

function bridgePayload(payload) {
  if (payload && typeof payload === "object" && payload.ok === false) {
    throw new Error(payload.message || "Dashboard Page API request failed.");
  }
  if (payload && typeof payload === "object" && payload.ok === true && "data" in payload) {
    return payload.data || {};
  }
  return payload || {};
}

async function bridgeRequest(path, method = "GET", body = null) {
  const bridge = await waitForDashboardBridge();
  await bridge.ready();
  const endpoint = bridgeEndpoint(path);
  let payload;
  if (method.toUpperCase() === "GET") {
    payload = await bridge.apiGet(endpoint, body || {});
  } else {
    payload = await bridge.apiPost(endpoint, body || {});
  }
  return bridgePayload(payload);
}

export async function apiRequest(path, method = 'GET', body = null, timeout = 30000) {
  if (isDashboardPage()) {
    return bridgeRequest(path, method, body);
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
