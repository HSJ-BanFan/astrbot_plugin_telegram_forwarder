import { store } from './store.js';
import { apiRequest } from './api.js';
import { escapeHtml } from './utils.js';
import { els, showToast, withAction, withButtonLoading, loadAll, saveConfig, enterApp } from './context.js';

export async function checkToken(token) {
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
    localStorage.setItem("telegram_forwarder_token", token);
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
    return;
  }

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
        return;
      }
    }
    if (!telegram.code_sent) {
      if (els.loginMessage) {
        els.loginMessage.textContent = "填写连接信息并发送验证码。";
      }
      return;
    }
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
    if (els.loginMessage) {
      els.loginMessage.textContent = "按顺序配置连接信息、发送验证码、提交验证码。";
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
  await loadAll();
  return result;
}

export function initLogin() {
  if (els.authForm) {
    els.authForm.addEventListener("submit", loginWithToken);
  }
  if (els.logoutBtn) {
    els.logoutBtn.addEventListener("click", () => {
      localStorage.removeItem("telegram_forwarder_token");
      window.location.reload();
    });
  }

  if (els.sendCodeBtn) {
    els.sendCodeBtn.addEventListener("click", () =>
      withButtonLoading(els.sendCodeBtn, "正在发送验证码...", async () => {
        await saveConfig({ quiet: true });
        const result = await apiRequest("/api/login/start", "POST", {
          phone: els.phoneInput.value.trim(),
          replace_existing: Boolean(store.state.status?.telegram?.replace_existing),
        });
        if (els.loginMessage) els.loginMessage.textContent = result.message || "";
        return result;
      }, "验证码已发送。")
    );
  }

  if (els.submitCodeBtn) {
    els.submitCodeBtn.addEventListener("click", () =>
      withButtonLoading(els.submitCodeBtn, "正在验证验证码...", async () => {
        const result = await apiRequest("/api/login/code", "POST", { code: els.codeInput.value.trim() });
        if (els.loginMessage) els.loginMessage.textContent = result.message || "";
        return result;
      }, "验证码已提交。")
    );
  }

  if (els.submitPasswordBtn) {
    els.submitPasswordBtn.addEventListener("click", () =>
      withButtonLoading(els.submitPasswordBtn, "正在登录...", async () => {
        const result = await apiRequest("/api/login/password", "POST", { password: els.passwordInput.value });
        if (els.loginMessage) els.loginMessage.textContent = result.message || "";
        return result;
      }, "密码已提交。")
    );
  }

  if (els.resetLoginBtn) {
    els.resetLoginBtn.addEventListener("click", () => {
      withButtonLoading(els.resetLoginBtn, "正在准备...", () => apiRequest("/api/login/reset", "POST"), "已进入重新登录流程。");
    });
  }

  if (els.exportSessionBtn) {
    els.exportSessionBtn.addEventListener("click", () =>
      withButtonLoading(els.exportSessionBtn, "正在导出...", exportSession, "登录信息已导出。")
    );
  }

  if (els.importSessionBtn && els.sessionImportFile) {
    els.importSessionBtn.addEventListener("click", () => {
      els.sessionImportFile.value = "";
      els.sessionImportFile.click();
    });
    els.sessionImportFile.addEventListener("change", () => {
      const file = els.sessionImportFile.files?.[0];
      if (!file) return;
      withButtonLoading(els.importSessionBtn, "正在导入...", () => importSessionFromFile(file), "登录信息已导入。");
    });
  }

  // subscribe to store changes to update login rendering
  store.subscribe(renderLogin);
}
