import { store } from './store.js';
import { apiRequest, isDashboardPage } from './api.js';
import { escapeHtml, safeStorageRemove, safeStorageSet } from './utils.js';
import { els, showToast, withAction, withButtonLoading, loadAll, enterApp } from './context.js';

const DEFAULT_PROXY_CONFIG = {
  protocol: "socks5",
  host: "",
  port: 0,
  username: "",
  password: "",
};

function normalizeProxyConfig(value) {
  const config = value && typeof value === "object" ? value : {};
  return {
    protocol: String(config.protocol || DEFAULT_PROXY_CONFIG.protocol),
    host: String(config.host || "").trim(),
    port: Number.parseInt(config.port, 10) || 0,
    username: String(config.username || "").trim(),
    password: String(config.password || ""),
  };
}

function proxyConfigFromInputs() {
  return normalizeProxyConfig({
    protocol: els.proxyProtocolInput?.value,
    host: els.proxyHostInput?.value,
    port: els.proxyPortInput?.value,
    username: els.proxyUsernameInput?.value,
    password: els.proxyPasswordInput?.value,
  });
}

function sameProxyConfig(left, right) {
  return JSON.stringify(normalizeProxyConfig(left)) === JSON.stringify(normalizeProxyConfig(right));
}

function syncLoginConfigInputs(config) {
  const proxy = normalizeProxyConfig(config?.proxy_config);
  if (els.apiIdInput) els.apiIdInput.value = config?.api_id || "";
  if (els.apiHashInput) els.apiHashInput.value = config?.api_hash || "";
  if (els.proxyProtocolInput) els.proxyProtocolInput.value = proxy.protocol;
  if (els.proxyHostInput) els.proxyHostInput.value = proxy.host;
  if (els.proxyPortInput) els.proxyPortInput.value = proxy.port || "";
  if (els.proxyUsernameInput) els.proxyUsernameInput.value = proxy.username;
  if (els.proxyPasswordInput) els.proxyPasswordInput.value = proxy.password;
}

async function saveChangedLoginConfig() {
  const loaded = store.state.config || {};
  const formApiId = Number.parseInt(els.apiIdInput?.value, 10) || 0;
  const formApiHash = els.apiHashInput?.value.trim() || "";
  const formProxy = proxyConfigFromInputs();
  const apiIdChanged = formApiId !== (Number.parseInt(loaded.api_id, 10) || 0);
  const apiHashChanged = formApiHash !== String(loaded.api_hash || "");
  const proxyChanged = !sameProxyConfig(formProxy, loaded.proxy_config);

  // 先读取服务器最新值，再把用户在当前页面真正改动过的字段重放上去。
  // 旧标签页因此不会在发送验证码前把整份过期配置写回。
  const latestResult = await apiRequest("/api/config");
  const latest = latestResult?.config || loaded;
  const patch = {};
  if (apiIdChanged) patch.api_id = formApiId;
  if (apiHashChanged) patch.api_hash = formApiHash;
  if (proxyChanged) patch.proxy_config = formProxy;

  let resolved = latest;
  if (Object.keys(patch).length > 0) {
    const result = await apiRequest("/api/config", "POST", { config: patch });
    resolved = result?.config || { ...latest, ...patch };
  }
  store.updateState({ config: resolved });
  syncLoginConfigInputs(resolved);
  return resolved;
}

export async function checkToken(token) {
  if (isDashboardPage()) return true;
  const response = await fetch("/api/auth/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  const payload = await response.json();
  return Boolean(payload?.data?.authorized);
}

export async function loginWithToken(event) {
  event.preventDefault();
  if (els.authError) els.authError.textContent = "";
  const token = els.tokenInput.value.trim();
  if (!token) {
    if (els.authError) els.authError.textContent = "请输入 Web Token。";
    return;
  }
  try {
    if (!(await checkToken(token))) {
      if (els.authError) els.authError.textContent = "Token 不正确。";
      return;
    }
    store.updateState({ token });
    safeStorageSet("telegram_forwarder_token", token);
    await enterApp();
  } catch (error) {
    if (els.authError) els.authError.textContent = error.message;
  }
}

export function updateLoginSteps() {
  const telegram = store.state.status?.telegram || {};
  const steps = document.querySelectorAll("[data-login-step]");
  steps.forEach((step) => {
    step.classList.remove("active", "done");
    step.hidden = true;
  });

  if (telegram.authorized && !telegram.replace_existing) {
    if (els.loginMessage) {
      els.loginMessage.textContent = "Telegram 已登录。需要切换账号时点击重新登录，新账号成功前不会清除当前登录。";
    }
    if (els.resetLoginBtn) els.resetLoginBtn.hidden = false;
  } else {
    if (els.resetLoginBtn) {
      els.resetLoginBtn.hidden = !telegram.authorized && !telegram.login_in_progress;
    }
    const connectStep = document.querySelector('[data-login-step="connect"]');
    const codeStep = document.querySelector('[data-login-step="code"]');
    const passwordStep = document.querySelector('[data-login-step="password"]');

    if (connectStep) {
      connectStep.hidden = false;
      connectStep.classList.add(telegram.code_sent ? "done" : "active");
    }

    if (telegram.login_in_progress) {
      if (telegram.replace_existing) {
        if (connectStep) connectStep.hidden = false;
        if (!telegram.code_sent) {
          if (connectStep) {
            connectStep.classList.remove("done");
            connectStep.classList.add("active");
          }
          if (els.loginMessage) {
            els.loginMessage.textContent = "当前账号仍然保留。填写新账号手机号并发送验证码，新账号成功后才会替换当前登录。";
          }
        }
      }
      if (telegram.code_sent) {
        if (codeStep) {
          codeStep.hidden = false;
          codeStep.classList.add(telegram.need_password ? "done" : "active");
        }
        if (els.loginMessage) {
          els.loginMessage.textContent = telegram.need_password ? "验证码已通过，请继续提交两步验证密码。" : "验证码已发送，请输入 Telegram 收到的验证码。";
        }
        if (telegram.need_password) {
          if (passwordStep) {
            passwordStep.hidden = false;
            passwordStep.classList.add("active");
          }
        }
      } else {
        if (!telegram.replace_existing && els.loginMessage) {
          els.loginMessage.textContent = "填写连接信息并发送验证码。";
        }
      }
    } else {
      if (els.loginMessage) {
        els.loginMessage.textContent = "按顺序配置连接信息、发送验证码、提交验证码。";
      }
    }
  }

  // Stepper Header progress linkage
  const nodeConnect = document.querySelector('[data-login-step-node="connect"]');
  const nodeCode = document.querySelector('[data-login-step-node="code"]');
  const nodePassword = document.querySelector('[data-login-step-node="password"]');

  [nodeConnect, nodeCode, nodePassword].forEach(node => {
    if (node) node.classList.remove("active", "done");
  });

  if (!telegram.login_in_progress && telegram.authorized && !telegram.replace_existing) {
    [nodeConnect, nodeCode, nodePassword].forEach(node => {
      if (node) node.classList.add("done");
    });
  } else {
    if (!telegram.code_sent) {
      if (nodeConnect) nodeConnect.classList.add("active");
    } else {
      if (nodeConnect) nodeConnect.classList.add("done");
      if (!telegram.need_password) {
        if (nodeCode) nodeCode.classList.add("active");
      } else {
        if (nodeCode) nodeCode.classList.add("done");
        if (nodePassword) nodePassword.classList.add("active");
      }
    }
  }
}

export function renderLogin() {
  const status = store.state.status || {};
  const telegram = status.telegram || {};
  const me = telegram.me;

  if (els.loginBadge) {
    els.loginBadge.textContent = telegram.authorized
      ? telegram.replace_existing
        ? "准备重新登录"
        : "已登录"
      : telegram.login_in_progress
        ? "登录中"
        : "未登录";
  }

  const accountTitle = telegram.authorized
    ? me?.username
      ? `@${me.username}`
      : me?.phone || "已授权账号"
    : "尚未登录 Telegram";
  const nickname = [me?.first_name, me?.last_name].filter(Boolean).join(" ") || "-";
  const accountDetail = telegram.authorized
    ? (nickname !== "-" ? nickname : me?.phone || "Session 可用")
    : telegram.login_in_progress
      ? telegram.replace_existing && !telegram.phone
        ? "准备重新登录，当前账号仍然保留"
        : `验证码流程进行中：${telegram.phone || "-"}`
      : "完成登录后将自动启动转发任务";
  const loginRows = telegram.authorized
    ? [
        ["账号", accountTitle],
        ["昵称", nickname],
        ["手机号", me?.phone || telegram.phone || "-"],
        ["用户 ID", me?.id || "-"],
        ["连接状态", telegram.connected ? "已连接" : "未连接"],
      ]
    : [
        ["状态", telegram.login_in_progress ? "登录流程中" : "未登录"],
        ["手机号", telegram.phone || "-"],
      ];

  if (els.loginAccountInfo) {
    els.loginAccountInfo.innerHTML = `
      <span class="account-pill">${escapeHtml(accountTitle)}</span>
      <span>${escapeHtml(accountDetail)}</span>
      <div class="account-detail-grid">
        ${loginRows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
      </div>
    `;
  }
  if (els.loginAccountCard) {
    els.loginAccountCard.hidden = Boolean(telegram.replace_existing);
  }

  updateLoginSteps();
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

export async function exportSession() {
  const data = await apiRequest("/api/export/session");
  downloadJson("telegram-forwarder-session.json", data);
  return { message: "登录信息已导出。" };
}

export async function importSessionFromFile(file) {
  const payload = await readJsonFile(file, "登录信息");
  const result = await apiRequest("/api/import/session", "POST", payload);
  // 导入/重登后必须 force 拉频道，否则可能仍命中未授权时的失败冷却空缓存。
  await loadAll({ force: true });
  return result;
}

/** 登录成功后 force 刷频道/群；流程中仅刷 status。 */
function loginRefreshMode(result) {
  return result?.authorized ? "force" : "status";
}

export function initLogin() {
  if (els.authForm && els.authForm.dataset.loginBound !== "true") {
    els.authForm.dataset.loginBound = "true";
    els.authForm.addEventListener("submit", loginWithToken);
  }
  if (els.logoutBtn && els.logoutBtn.dataset.loginBound !== "true") {
    els.logoutBtn.dataset.loginBound = "true";
    els.logoutBtn.addEventListener("click", () => {
      safeStorageRemove("telegram_forwarder_token");
      window.location.reload();
    });
  }

  if (els.sendCodeBtn && els.sendCodeBtn.dataset.loginBound !== "true") {
    els.sendCodeBtn.dataset.loginBound = "true";
    els.sendCodeBtn.addEventListener("click", () =>
      // 登录相关动作后强制刷一次状态（含 me 缓存），保证昵称/步骤及时更新。
      withButtonLoading(els.sendCodeBtn, "正在发送验证码...", async () => {
        await saveChangedLoginConfig();
        const result = await apiRequest("/api/login/start", "POST", {
          phone: els.phoneInput.value.trim(),
          replace_existing: Boolean(store.state.status?.telegram?.replace_existing),
        });
        if (els.loginMessage) els.loginMessage.textContent = result.message || "";
        return result;
      }, "验证码已发送。", { refresh: "status" })
    );
  }

  const runProxyTest = (button, resultElement, mode) => {
    if (!button || !resultElement) return;
    if (button.dataset.proxyTestBound === "true") return;
    button.dataset.proxyTestBound = "true";
    button.addEventListener("click", async () => {
      const originalText = button.textContent;
      button.disabled = true;
      button.textContent = "测试中...";
      resultElement.className = "proxy-test-result";
      resultElement.textContent = "测试中...";
      try {
        const result = await apiRequest("/api/proxy/test", "POST", {
          mode,
          proxy_config: proxyConfigFromInputs(),
        }, 12000);
        const success = Boolean(result.success) && Number.isFinite(result.latency_ms);
        resultElement.classList.add(success ? "success" : "timeout");
        resultElement.textContent = success ? `${result.latency_ms} ms` : (result.message || "连接失败");
      } catch (error) {
        resultElement.classList.add("timeout");
        resultElement.textContent = error.message || "连接失败";
      } finally {
        button.disabled = false;
        button.textContent = originalText;
      }
    });
  };

  runProxyTest(els.proxyConnectivityBtn, els.proxyConnectivityResult, "connectivity");
  runProxyTest(els.proxyQualityBtn, els.proxyQualityResult, "quality");

  if (els.submitCodeBtn && els.submitCodeBtn.dataset.loginBound !== "true") {
    els.submitCodeBtn.dataset.loginBound = "true";
    els.submitCodeBtn.addEventListener("click", () =>
      withButtonLoading(els.submitCodeBtn, "正在验证验证码...", async () => {
        const result = await apiRequest("/api/login/code", "POST", { code: els.codeInput.value.trim() });
        if (els.loginMessage) els.loginMessage.textContent = result.message || "";
        return result;
      }, "验证码已提交。", { refresh: loginRefreshMode })
    );
  }

  if (els.submitPasswordBtn && els.submitPasswordBtn.dataset.loginBound !== "true") {
    els.submitPasswordBtn.dataset.loginBound = "true";
    els.submitPasswordBtn.addEventListener("click", () =>
      withButtonLoading(els.submitPasswordBtn, "正在登录...", async () => {
        const result = await apiRequest("/api/login/password", "POST", { password: els.passwordInput.value });
        if (els.loginMessage) els.loginMessage.textContent = result.message || "";
        return result;
      }, "密码已提交。", { refresh: loginRefreshMode })
    );
  }

  if (els.resetLoginBtn && els.resetLoginBtn.dataset.loginBound !== "true") {
    els.resetLoginBtn.dataset.loginBound = "true";
    els.resetLoginBtn.addEventListener("click", () => {
      withButtonLoading(
        els.resetLoginBtn,
        "正在准备重新登录...",
        () => apiRequest("/api/login/reset", "POST"),
        "已进入重新登录流程。",
        { refresh: "status" }
      );
    });
  }

  if (els.exportSessionBtn && els.exportSessionBtn.dataset.loginBound !== "true") {
    els.exportSessionBtn.dataset.loginBound = "true";
    els.exportSessionBtn.addEventListener("click", () =>
      withButtonLoading(els.exportSessionBtn, "正在导出...", exportSession, "登录信息已导出。")
    );
  }

  if (els.importSessionBtn && els.sessionImportFile && els.importSessionBtn.dataset.loginBound !== "true") {
    els.importSessionBtn.dataset.loginBound = "true";
    els.importSessionBtn.addEventListener("click", () => {
      els.sessionImportFile.value = "";
      els.sessionImportFile.click();
    });
    els.sessionImportFile.addEventListener("change", () => {
      const file = els.sessionImportFile.files?.[0];
      if (!file) return;
      withButtonLoading(
        els.importSessionBtn,
        "正在导入...",
        () => importSessionFromFile(file),
        "登录信息已导入。",
        // importSessionFromFile 内已 force loadAll，避免重复。
        { refresh: false }
      );
    });
  }

  if (els.clearSessionBtn && els.clearSessionBtn.dataset.loginBound !== "true") {
    els.clearSessionBtn.dataset.loginBound = "true";
    els.clearSessionBtn.addEventListener("click", () => {
      if (
        !window.confirm(
          "确认清空本地 Telegram 登录信息？清空后需重新验证码登录或导入会话。"
        )
      ) {
        return;
      }
      withButtonLoading(
        els.clearSessionBtn,
        "正在清空登录信息...",
        async () => {
          const result = await apiRequest("/api/login/clear-session", "POST");
          if (els.loginMessage) {
            els.loginMessage.textContent =
              result.message || "已清空本地登录信息，请重新发送验证码或导入。";
          }
          return result;
        },
        "已清空登录信息。",
        { refresh: "force" }
      );
    });
  }

  // subscribe to store changes to update login rendering
  if (!store._loginRenderBound) {
    store._loginRenderBound = true;
    store.subscribe(renderLogin);
  }
}
