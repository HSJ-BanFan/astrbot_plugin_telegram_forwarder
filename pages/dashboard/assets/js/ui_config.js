import { store } from './store.js';
import { isDashboardPage } from './api.js';
import { els } from './context.js';
import { FORWARD_GROUPS, FORWARD_FIELDS } from './config.js';
import { renderTopologySurfaces } from './ui_topology.js';
import { renderQQTargetSelector, renderTGChannelSelector, splitList, joinList } from './ui_selector.js';
import { escapeHtml, motionEnabled } from './utils.js';

const DEFAULT_WEB_CONFIG = { enabled: true, host: "127.0.0.1", port: 8180, token: "" };
const DEFAULT_PROXY_CONFIG = { protocol: "socks5", host: "", port: 0, username: "", password: "" };

function proxyConfigFromState(cfg) {
  if (cfg.proxy_config && typeof cfg.proxy_config === "object") {
    return { ...DEFAULT_PROXY_CONFIG, ...cfg.proxy_config };
  }
  if (!cfg.proxy) return { ...DEFAULT_PROXY_CONFIG };
  try {
    const parsed = new URL(cfg.proxy);
    return {
      protocol: parsed.protocol.replace(":", "").toLowerCase() || "socks5",
      host: parsed.hostname || "",
      port: Number.parseInt(parsed.port, 10) || 0,
      username: decodeURIComponent(parsed.username || ""),
      password: decodeURIComponent(parsed.password || ""),
    };
  } catch {
    return { ...DEFAULT_PROXY_CONFIG };
  }
}

export function intValue(id, fallback = 0) {
  const parsed = Number.parseInt(els[id]?.value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function currentWebConfig() {
  return { ...DEFAULT_WEB_CONFIG, ...(store.state.config?.web_config || {}) };
}

export function renderSidebarStatusCard() {
  if (!els.sidebarFooter) return;
  const web = currentWebConfig();
  const enabled = Boolean(web.enabled);
  const host = web.host || "127.0.0.1";
  const port = web.port || 8180;

  const showHost = host === "0.0.0.0" ? (window.location.hostname || "localhost") : host;
  const webUrl = `http://${showHost}:${port}/`;

  if (enabled) {
    const isDashboard = isDashboardPage();
    const isLoopback = host === "127.0.0.1" || host === "localhost" || host === "::1";
    const statusLabel = isDashboard
      ? `已连接官方插件页 API，独立 WebUI${isLoopback ? "（本地回环地址，仅当前环境内可达）" : ""}:`
      : "独立 WebUI 运行中:";
    const badgeLabel = isDashboard ? "插件页正常" : "运行正常";

    els.sidebarFooter.innerHTML = `
      <div class="sidebar-status-card">
        <div class="status-badge success">
          <span class="status-dot success" style="width: 6px; height: 6px; background: var(--success); border-radius: 50%; display: inline-block; margin-right: 6px; vertical-align: middle;"></span>
          ${escapeHtml(badgeLabel)}
        </div>
        <div class="status-text">${escapeHtml(statusLabel)}</div>
        <a href="${escapeHtml(webUrl)}" target="_blank" class="status-link">${escapeHtml(webUrl)}</a>
      </div>
    `;

    if (window.gsap && motionEnabled()) {
      const dot = els.sidebarFooter.querySelector(".status-dot");
      if (dot) {
        window.gsap.killTweensOf(dot);
        window.gsap.to(dot, {
          scale: 1.4,
          opacity: 0.5,
          duration: 1.2,
          repeat: -1,
          yoyo: true,
          ease: "power1.inOut"
        });
      }
    }
  } else {
    els.sidebarFooter.innerHTML = `
      <div class="sidebar-status-card">
        <div class="status-badge disabled">已关闭</div>
        <div class="status-text">Web 独立控制台未启用。您可以前往「Web 设置」开启。</div>
      </div>
    `;
  }
}

export function renderRootConfig() {
  const cfg = store.state.config || {};
  if (els.apiIdInput) els.apiIdInput.value = cfg.api_id || "";
  if (els.apiHashInput) els.apiHashInput.value = cfg.api_hash || "";
  if (els.phoneInput) els.phoneInput.value = cfg.phone || "";
  const proxy = proxyConfigFromState(cfg);
  if (els.proxyProtocolInput) els.proxyProtocolInput.value = proxy.protocol;
  if (els.proxyHostInput) els.proxyHostInput.value = proxy.host;
  if (els.proxyPortInput) els.proxyPortInput.value = proxy.port || "";
  if (els.proxyUsernameInput) els.proxyUsernameInput.value = proxy.username;
  if (els.proxyPasswordInput) els.proxyPasswordInput.value = proxy.password;
  if (els.targetChannelInput) els.targetChannelInput.value = cfg.target_channel || "";
  if (els.targetQQInput) els.targetQQInput.value = joinList(cfg.target_qq_session || []);

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
  if (els.debugDefaultInput) els.debugDefaultInput.checked = Boolean(cfg.debug_enabled_default);

  const web = currentWebConfig();
  if (els.webEnabledInput) els.webEnabledInput.checked = Boolean(web.enabled);
  if (els.webHostInput) els.webHostInput.value = web.host;
  if (els.webPortInput) els.webPortInput.value = web.port;
  if (els.webTokenInput) els.webTokenInput.value = web.token;
}

let forwardTabResizeObserver = null;

export function renderForwardTabs() {
  if (!els.forwardTabs) return;

  // Clean up previous indicator state initially to force fallback safety background
  els.forwardTabs.classList.remove("indicator-positioned");

  const buttonsHtml = FORWARD_GROUPS.map(
    (group) =>
      `<button type="button" data-forward-tab="${group.id}" class="${store.state.forwardGroup === group.id ? "active" : ""}">${escapeHtml(group.label)}</button>`,
  ).join("");

  els.forwardTabs.innerHTML = `
    <div class="segmented-indicator" id="forwardTabIndicator"></div>
    ${buttonsHtml}
  `;

  els.forwardTabs.querySelectorAll("[data-forward-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      collectForwardConfig();
      store.updateState({ forwardGroup: button.dataset.forwardTab });
      renderForwardTabs();
      renderForwardConfig();
    });
  });

  syncForwardTabIndicator();

  // Set up ResizeObserver to recalculate offsets once the iframe layout resolves
  if (window.ResizeObserver) {
    if (forwardTabResizeObserver) {
      forwardTabResizeObserver.disconnect();
    }
    forwardTabResizeObserver = new ResizeObserver(() => {
      syncForwardTabIndicator();
    });
    forwardTabResizeObserver.observe(els.forwardTabs);
  }
}

function syncForwardTabIndicator() {
  if (!els.forwardTabs) return;
  const activeBtn = els.forwardTabs.querySelector("button.active");
  const indicator = els.forwardTabs.querySelector("#forwardTabIndicator");
  if (!activeBtn || !indicator) return;

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (!activeBtn.isConnected || !indicator.isConnected) return;
      const targetLeft = activeBtn.offsetLeft;
      const targetWidth = activeBtn.offsetWidth;
      if (!targetWidth) {
        // Fallback: If layout is not resolved, hide sliding background to let CSS fallback show
        els.forwardTabs.classList.remove("indicator-positioned");
        return;
      }

      if (window.gsap && motionEnabled()) {
        const isFirstTime = !indicator.style.width || indicator.style.width === "0px";
        if (isFirstTime) {
          window.gsap.set(indicator, {
            left: targetLeft,
            width: targetWidth,
            onComplete: () => {
              els.forwardTabs.classList.add("indicator-positioned");
            }
          });
        } else {
          window.gsap.to(indicator, {
            left: targetLeft,
            width: targetWidth,
            duration: 0.3,
            ease: "power2.out",
            onStart: () => {
              els.forwardTabs.classList.add("indicator-positioned");
            }
          });
        }
      } else {
        indicator.style.left = `${targetLeft}px`;
        indicator.style.width = `${targetWidth}px`;
        els.forwardTabs.classList.add("indicator-positioned");
      }
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
  if (field.type === "textarea") {
    return `
      <div class="settings-card wide">
        <h3>${escapeHtml(field.label)}</h3>
        <textarea ${attr} rows="8">${escapeHtml(value ?? "")}</textarea>
      </div>
    `;
  }
  const inputType = field.type === "int" || field.type === "float" ? "number" : field.type === "password" ? "password" : "text";
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

  // Staggered cascade entrance animation for config cards
  if (window.gsap && motionEnabled()) {
    const cards = els.forwardConfigForm.querySelectorAll(".settings-card");
    if (cards.length) {
      window.gsap.killTweensOf(cards);
      window.gsap.fromTo(cards,
        { opacity: 0, y: 15 },
        { opacity: 1, y: 0, duration: 0.35, stagger: 0.04, ease: "power2.out" }
      );
    }
  }
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
  store.state.config.proxy_config = {
    protocol: els.proxyProtocolInput?.value || DEFAULT_PROXY_CONFIG.protocol,
    host: els.proxyHostInput?.value.trim() || "",
    port: intValue("proxyPortInput", 0),
    username: els.proxyUsernameInput?.value.trim() || "",
    password: els.proxyPasswordInput?.value || "",
  };
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
      cfg[field.key] = field.type === "password" ? el.value : el.value.trim();
    }
  });
  store.state.config.forward_config = cfg;
}
