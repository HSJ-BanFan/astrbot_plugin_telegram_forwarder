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

export function targetFromGroup(groupId) {
  const group = store.state.qqGroups.find((item) => String(item.group_id) === String(groupId));
  const session = String(group?.session || "").trim();
  return session || String(groupId || "").trim();
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

function selectorScrollKey(node, index) {
  if (node.classList.contains("selector-chip-row")) return "chip-row";
  if (node.classList.contains("selector-list")) return "list";
  if (node.classList.contains("selector-selected")) return "selected";
  return `node-${index}`;
}

function captureSelectorScroll(root) {
  const scrollState = {};
  root.querySelectorAll(".selector-chip-row, .selector-list, .selector-selected").forEach((node, index) => {
    scrollState[selectorScrollKey(node, index)] = {
      left: node.scrollLeft,
      top: node.scrollTop,
    };
  });
  return scrollState;
}

function restoreSelectorScroll(root, scrollState) {
  root.querySelectorAll(".selector-chip-row, .selector-list, .selector-selected").forEach((node, index) => {
    const state = scrollState[selectorScrollKey(node, index)];
    if (!state) return;
    node.scrollLeft = state.left;
    node.scrollTop = state.top;
  });
}

export function renderQQTargetSelector({ root, manualInput, inheritLabel = "未配置默认 QQ 目标", compact = false }) {
  if (!root || !manualInput) return;
  const scrollState = captureSelectorScroll(root);
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
  const compactManualTargets = targets
    .filter((target) => !groupByTarget(target))
    .map((target) => `
      <button type="button" class="selector-chip selected" data-remove-target="${escapeHtml(target)}">
        <span>${escapeHtml(target)}</span>
        <small>manual</small>
      </button>
    `)
    .join("");

  const visibleGroups = compact
    ? [...groups].sort((a, b) =>
        Number(Boolean(targetForGroup(targets, b.group_id))) - Number(Boolean(targetForGroup(targets, a.group_id))),
      )
    : groups;
  const listHtml = visibleGroups.length
    ? visibleGroups
        .map((group) => {
          const selected = Boolean(targetForGroup(targets, group.group_id));
          return compact
            ? `
              <button type="button" class="selector-chip ${selected ? "selected" : ""}" data-qq-group="${escapeHtml(group.group_id)}">
                <span>${escapeHtml(group.group_name || `群 ${group.group_id}`)}</span>
                <small>${selected ? "已选" : escapeHtml(group.source || "live")}</small>
              </button>
            `
            : `
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
    : '<div class="selector-empty">没有可显示的 QQ 群。</div>';
  const compactListHtml = compactManualTargets ? `${compactManualTargets}${listHtml}` : listHtml;

  root.innerHTML = `
    <div class="selector-toolbar">
      <input data-selector-search type="search" placeholder="搜索 QQ 群名或群号" value="${escapeHtml(root.dataset.search || "")}" />
      <button class="btn btn-soft" data-selector-refresh type="button">刷新群列表</button>
    </div>
    <div class="selector-status">${escapeHtml(statusText)}</div>
    ${
      compact
        ? `
          <div class="selector-chip-row">
            ${compactListHtml}
          </div>
        `
        : `
          <div class="selector-layout">
            <div class="selector-list">
              ${listHtml}
            </div>
            <div class="selector-selected">
              ${selectedHtml}
            </div>
          </div>
        `
    }
  `;
  restoreSelectorScroll(root, scrollState);

  // Search input
  const searchInput = root.querySelector("[data-selector-search]");
  if (searchInput) {
    searchInput.addEventListener("input", (event) => {
      root.dataset.search = event.target.value;
      renderQQTargetSelector({ root, manualInput, inheritLabel, compact });
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
        : [...current, targetFromGroup(groupId)];
      manualInput.value = joinList(next);
      renderQQTargetSelector({ root, manualInput, inheritLabel, compact });
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
      renderQQTargetSelector({ root, manualInput, inheritLabel, compact });
      // trigger change event to notify potential listeners
      const event = new Event('change');
      manualInput.dispatchEvent(event);
    });
  });
}

export function renderTGChannelSelector({ root, manualInput, compact = false }) {
  if (!root || !manualInput) return;
  const scrollState = captureSelectorScroll(root);
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
  const selectedChannel = currentRef ? channelByRef(currentRef) : null;
  const selectedLabel = selectedChannel
    ? selectedChannel.title || selectedChannel.username || selectedChannel.channel_ref
    : manualInput.value.trim();
  const selectedMeta = selectedChannel
    ? `${selectedChannel.username ? `@${selectedChannel.username}` : selectedChannel.channel_ref} · ${selectedChannel.kind || "channel"}`
    : currentRef ? "manual" : "";
  const selectedHtml = currentRef
    ? `
      <button type="button" class="selector-pill" data-clear-tg-target>
        <span>${escapeHtml(selectedLabel)}</span>
        <small>${escapeHtml(selectedMeta)}</small>
      </button>
    `
    : '<div class="selector-empty">未配置 Telegram 目标</div>';
  const compactManualTarget = currentRef && !selectedChannel
    ? `
      <button type="button" class="selector-chip selected" data-clear-tg-target>
        <span>${escapeHtml(selectedLabel)}</span>
        <small>manual</small>
      </button>
    `
    : "";
  
  const visibleChannels = compact
    ? [...channels].sort((a, b) =>
        Number(String(b.channel_ref || "") === currentRef) - Number(String(a.channel_ref || "") === currentRef),
      )
    : channels;
  const listHtml = visibleChannels.length
    ? visibleChannels
        .map((channel) => {
          const ref = channel.channel_ref || "";
          const selected = String(ref) === currentRef;
          const handle = channel.username ? `@${channel.username}` : ref;
          return compact
            ? `
              <button type="button" class="selector-chip ${selected ? "selected" : ""}" data-tg-channel="${escapeHtml(ref)}">
                <span>${escapeHtml(channel.title || handle)}</span>
                <small>${selected ? "已选" : escapeHtml(handle)}</small>
              </button>
            `
            : `
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
    : '<div class="selector-empty">没有可显示的 Telegram 频道。</div>';
  const compactListHtml = compactManualTarget ? `${compactManualTarget}${listHtml}` : listHtml;

  root.innerHTML = `
    <div class="selector-toolbar">
      <input data-selector-search type="search" placeholder="搜索频道标题、用户名或 ID" value="${escapeHtml(root.dataset.search || "")}" />
      <button class="btn btn-soft" data-selector-refresh type="button">刷新频道</button>
    </div>
    <div class="selector-status">${escapeHtml(statusText)}</div>
    ${
      compact
        ? `
          <div class="selector-chip-row">
            ${compactListHtml}
          </div>
        `
        : `
          <div class="selector-layout">
            <div class="selector-list">
              ${listHtml}
            </div>
            <div class="selector-selected">
              ${selectedHtml}
            </div>
          </div>
        `
    }
  `;
  restoreSelectorScroll(root, scrollState);

  // Search input
  const searchInput = root.querySelector("[data-selector-search]");
  if (searchInput) {
    searchInput.addEventListener("input", (event) => {
      root.dataset.search = event.target.value;
      renderTGChannelSelector({ root, manualInput, compact });
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
      renderTGChannelSelector({ root, manualInput, compact });
      // trigger change event to notify potential listeners
      const event = new Event('change');
      manualInput.dispatchEvent(event);
    });
  });

  root.querySelectorAll("[data-clear-tg-target]").forEach((button) => {
    button.addEventListener("click", () => {
      manualInput.value = "";
      renderTGChannelSelector({ root, manualInput, compact });
      const event = new Event('change');
      manualInput.dispatchEvent(event);
    });
  });
}
