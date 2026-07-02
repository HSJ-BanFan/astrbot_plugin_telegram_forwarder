import { store } from './store.js';
import { escapeHtml } from './utils.js';
import { showToast, loadQQGroups, loadTGChannels } from './context.js';
import { collectForms, renderAll } from '../app.js';

export function splitList(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(String).map((v) => v.trim()).filter(Boolean);
  return String(value).split(/[\n,]/).map((v) => v.trim()).filter(Boolean);
}

export function joinList(value) {
  return Array.isArray(value) ? value.join("\n") : "";
}

export function uniqueList(items) {
  const seen = new Set();
  return items.filter((item) => {
    const value = String(item || "").trim();
    if (!value || seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}

export function isNumericGroupTarget(target) {
  return /^\d+$/.test(String(target || "").trim());
}

export function groupIdFromTarget(target) {
  const value = String(target || "").trim();
  if (isNumericGroupTarget(value)) return value;
  const parts = value.split(":");
  if (parts.length >= 3 && parts[1] === "GroupMessage" && /^\d+$/.test(parts[2])) {
    return parts[2];
  }
  return "";
}

export function groupByTarget(target) {
  const groupId = groupIdFromTarget(target);
  if (!groupId) return null;
  return store.state.qqGroups.find((group) => String(group.group_id) === groupId) || null;
}

export function targetForGroup(targets, groupId) {
  return targets.find((target) => groupIdFromTarget(target) === String(groupId));
}

export function channelByRef(ref) {
  const value = String(ref || "").trim().replace(/^@/, "");
  if (!value) return null;
  return store.state.tgChannels.find((channel) => String(channel.channel_ref) === value) || null;
}

export function channelTitleUI(ref) {
  const channel = channelByRef(ref);
  if (channel) return channel.title || channel.channel_ref;
  const value = String(ref || "").trim();
  if (!value) return "新频道";
  return value.startsWith("-") ? value : `@${value.replace(/^@/, "")}`;
}

export function renderQQTargetSelector({ root, manualInput, inheritLabel = "未配置默认 QQ 目标" }) {
  if (!root || !manualInput) return;
  const targets = uniqueList(splitList(manualInput.value));
  const keyword = String(root.dataset.search || "").trim().toLowerCase();
  const groups = store.state.qqGroups.filter((group) => {
    if (!keyword) return true;
    return (
      String(group.group_id || "").toLowerCase().includes(keyword) ||
      String(group.group_name || "").toLowerCase().includes(keyword)
    );
  });
  const statusText = store.state.qqGroupsAvailable
    ? `${store.state.qqGroups.length} 个 QQ 群`
    : store.state.qqGroupsMessage || "QQ 平台未就绪";
  const selectedHtml = targets.length
    ? targets
        .map((target) => {
          const group = groupByTarget(target);
          const label = group
            ? `${group.group_name || `群 ${group.group_id}`} (${group.group_id})`
            : target;
          const badge = group ? group.source || "live" : "manual";
          return `
            <button type="button" class="selector-pill" data-remove-target="${escapeHtml(target)}">
              <span>${escapeHtml(label)}</span>
              <small>${escapeHtml(badge)}</small>
            </button>
          `;
        })
        .join("")
    : `<div class="selector-empty">${escapeHtml(inheritLabel)}</div>`;

  root.innerHTML = `
    <div class="selector-toolbar">
      <input data-selector-search type="search" placeholder="搜索 QQ 群名或群号" value="${escapeHtml(root.dataset.search || "")}" />
      <button class="btn btn-soft" data-selector-refresh type="button">刷新群列表</button>
    </div>
    <div class="selector-status">${escapeHtml(statusText)}</div>
    <div class="selector-layout">
      <div class="selector-list">
        ${
          groups.length
            ? groups
                .map((group) => {
                  const selected = Boolean(targetForGroup(targets, group.group_id));
                  return `
                    <button type="button" class="selector-row ${selected ? "selected" : ""}" data-qq-group="${escapeHtml(group.group_id)}">
                      <span>
                        <strong>${escapeHtml(group.group_name || `群 ${group.group_id}`)}</strong>
                        <small>${escapeHtml(group.group_id)} · ${escapeHtml(group.member_count ?? 0)} 人</small>
                      </span>
                      <em>${selected ? "已选" : escapeHtml(group.source || "live")}</em>
                    </button>
                  `;
                })
                .join("")
            : '<div class="selector-empty">没有可显示的 QQ 群。</div>'
        }
      </div>
      <div class="selector-selected">
        ${selectedHtml}
      </div>
    </div>
  `;

  // Search input
  const searchInput = root.querySelector("[data-selector-search]");
  if (searchInput) {
    searchInput.addEventListener("input", (event) => {
      root.dataset.search = event.target.value;
      renderQQTargetSelector({ root, manualInput, inheritLabel });
    });
  }

  // Refresh button
  const refreshBtn = root.querySelector("[data-selector-refresh]");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      collectForms();
      await loadQQGroups({ force: true });
      renderAll();
      showToast("QQ 群列表已刷新。");
    });
  }

  // QQ group click binding
  root.querySelectorAll("[data-qq-group]").forEach((button) => {
    button.addEventListener("click", () => {
      const groupId = button.dataset.qqGroup;
      const current = uniqueList(splitList(manualInput.value));
      const existing = targetForGroup(current, groupId);
      const next = existing
        ? current.filter((target) => target !== existing)
        : [...current, groupId];
      manualInput.value = joinList(next);
      renderQQTargetSelector({ root, manualInput, inheritLabel });
      // trigger change event to notify potential listeners
      const event = new Event('change');
      manualInput.dispatchEvent(event);
    });
  });

  // Remove target click binding
  root.querySelectorAll("[data-remove-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.removeTarget;
      manualInput.value = joinList(
        uniqueList(splitList(manualInput.value)).filter((item) => item !== target),
      );
      renderQQTargetSelector({ root, manualInput, inheritLabel });
      // trigger change event to notify potential listeners
      const event = new Event('change');
      manualInput.dispatchEvent(event);
    });
  });
}

export function renderTGChannelSelector({ root, manualInput }) {
  if (!root || !manualInput) return;
  const currentRef = String(manualInput.value || "").trim().replace(/^@/, "");
  const keyword = String(root.dataset.search || "").trim().toLowerCase();
  const channels = store.state.tgChannels.filter((channel) => {
    if (!keyword) return true;
    return (
      String(channel.title || "").toLowerCase().includes(keyword) ||
      String(channel.username || "").toLowerCase().includes(keyword) ||
      String(channel.channel_ref || "").toLowerCase().includes(keyword)
    );
  });
  const statusText = store.state.tgChannelsAvailable
    ? `${store.state.tgChannels.length} 个 Telegram 频道`
    : store.state.tgChannelsMessage || "Telegram 未登录或未授权";
  
  root.innerHTML = `
    <div class="selector-toolbar">
      <input data-selector-search type="search" placeholder="搜索频道标题、用户名或 ID" value="${escapeHtml(root.dataset.search || "")}" />
      <button class="btn btn-soft" data-selector-refresh type="button">刷新频道</button>
    </div>
    <div class="selector-status">${escapeHtml(statusText)}</div>
    <div class="selector-list">
      ${
        channels.length
          ? channels
              .map((channel) => {
                const selected = String(channel.channel_ref) === currentRef;
                const ref = channel.channel_ref || "";
                const handle = channel.username ? `@${channel.username}` : ref;
                return `
                  <button type="button" class="selector-row ${selected ? "selected" : ""}" data-tg-channel="${escapeHtml(ref)}">
                    <span>
                      <strong>${escapeHtml(channel.title || handle)}</strong>
                      <small>${escapeHtml(handle)} · ${escapeHtml(channel.kind || "channel")}</small>
                    </span>
                    <em>${selected ? "已选" : escapeHtml(channel.source || "live")}</em>
                  </button>
                `;
              })
              .join("")
          : '<div class="selector-empty">没有可显示的 Telegram 频道。</div>'
      }
    </div>
  `;

  // Search input
  const searchInput = root.querySelector("[data-selector-search]");
  if (searchInput) {
    searchInput.addEventListener("input", (event) => {
      root.dataset.search = event.target.value;
      renderTGChannelSelector({ root, manualInput });
    });
  }

  // Refresh button
  const refreshBtn = root.querySelector("[data-selector-refresh]");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      collectForms();
      await loadTGChannels({ force: true });
      renderAll();
      showToast("Telegram 频道列表已刷新。");
    });
  }

  // TG channel click binding
  root.querySelectorAll("[data-tg-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      manualInput.value = button.dataset.tgChannel || "";
      renderTGChannelSelector({ root, manualInput });
      // trigger change event to notify potential listeners
      const event = new Event('change');
      manualInput.dispatchEvent(event);
    });
  });
}
