import { store } from './store.js';
import { els, showToast } from './context.js';
import { defaultChannel, renderChannels } from './ui_channels.js';
import {
  renderSelectorChip,
  splitList,
  joinList,
  uniqueList,
  groupByTarget,
  groupIdFromTarget,
  channelTitleUI,
  renderQQTargetSelector,
} from './ui_selector.js';
import { bindLiveSearchInput, escapeHtml, channelKey, motionEnabled, fieldsMatchSearch } from './utils.js';

export const TOPOLOGY_FILTERS = new Set(["all", "active", "dedicated", "inherited", "unlinked"]);
export const topologyUiState = {
  query: "",
  filter: "all",
  paletteChannelsQuery: "",
  paletteGroupsQuery: "",
};

let openSectionHandler = null;

export function setTopologySectionHandler(handler) {
  openSectionHandler = typeof handler === "function" ? handler : null;
}

function normalizeChannelRef(value) {
  return String(value || "").trim().replace(/^[@#]/, "");
}

function targetLabel(target) {
  const group = groupByTarget(target);
  if (group) {
    return `${group.group_name || `群 ${group.group_id}`} (${group.group_id})`;
  }
  const groupId = groupIdFromTarget(target);
  return groupId ? `QQ群 ${groupId}` : String(target || "未命名 QQ 目标");
}

function queueCountForChannel(channel) {
  const ref = normalizeChannelRef(channel?.channel_username || "");
  if (!ref) return 0;
  const queueByChannel = store.state.status?.queue?.by_channel || {};
  const candidates = new Set([ref, `@${ref}`, channelTitleUI(ref)]);
  return Object.entries(queueByChannel).reduce((total, [key, count]) => {
    const normalized = normalizeChannelRef(key);
    if (candidates.has(key) || normalized === ref) {
      return total + (Number.parseInt(count, 10) || 0);
    }
    return total;
  }, 0);
}

const TOPOLOGY_ROW_HEIGHT = 74;
const TOPOLOGY_TOP_PADDING = 70;
const TOPOLOGY_BOTTOM_PADDING = 70;
const topologyPointer = {
  x: null,
  y: null,
};
let topologyContextMenu = null;
let topologyContextCleanup = null;

function topologyY(index) {
  return TOPOLOGY_TOP_PADDING + index * TOPOLOGY_ROW_HEIGHT;
}

/* —— 拓扑布局：二分图重心（barycenter）排序 ——
   源节点按其目标的初始位置重心排序，让共享同一 QQ 目标的频道相邻成簇；
   未连接任何目标的源沉底。这是消除连线交叉的第一步。 */
function topologySortSources(sourceItems) {
  const prelimIndex = new Map();
  sourceItems.forEach((source) => {
    source.targets.forEach((target) => {
      const key = String(target || "").trim();
      if (key && !prelimIndex.has(key)) prelimIndex.set(key, prelimIndex.size);
    });
  });
  return sourceItems
    .map((source, order) => {
      const positions = source.targets
        .map((target) => prelimIndex.get(String(target || "").trim()))
        .filter((position) => position != null);
      const barycenter = positions.length
        ? positions.reduce((sum, position) => sum + position, 0) / positions.length
        : Number.POSITIVE_INFINITY;
      return { source, order, barycenter };
    })
    .sort((a, b) => a.barycenter - b.barycenter || a.order - b.order)
    .map((entry) => entry.source);
}

/* 目标节点垂直位置 = 其全部来源行的重心，再做自上而下的最小间距推压；
   连线因此变成短而平的扇形，而不是全部斜穿画布汇聚到顶部。 */
function topologyTargetYLayout(sourceItems, targetIndex, targetCount) {
  const sums = new Array(targetCount).fill(0);
  const counts = new Array(targetCount).fill(0);
  sourceItems.forEach((source, row) => {
    source.targets.forEach((target) => {
      const position = targetIndex.get(String(target || "").trim());
      if (position == null) return;
      sums[position] += topologyY(row);
      counts[position] += 1;
    });
  });
  const placed = sums
    .map((sum, position) => ({
      position,
      y: counts[position] ? sum / counts[position] : topologyY(position),
    }))
    .sort((a, b) => a.y - b.y || a.position - b.position);
  let cursor = TOPOLOGY_TOP_PADDING;
  placed.forEach((item) => {
    item.y = Math.max(item.y, cursor);
    cursor = item.y + TOPOLOGY_ROW_HEIGHT;
  });
  const ys = new Array(targetCount).fill(TOPOLOGY_TOP_PADDING);
  placed.forEach((item) => {
    ys[item.position] = item.y;
  });
  return ys;
}

function topologyNode(label, meta, side, index, attrs = "", badge = "", y = topologyY(index)) {
  const badgeClass = badge ? " has-badge" : "";
  const badgeHtml = badge
    ? `<em class="topology-node-badge" aria-hidden="true">${escapeHtml(badge)}</em>`
    : "";
  return `
    <button class="topology-node topology-node-${side}${badgeClass}" style="--topology-y: ${y}px" type="button" ${attrs}>
      <strong>${escapeHtml(label)}</strong>
      <span>${escapeHtml(meta)}</span>
      ${badgeHtml}
    </button>
  `;
}

function topologyDragData(type, value) {
  return escapeHtml(JSON.stringify({ type, value }));
}

function topologyMatchesFilter(source, filter) {
  if (filter === "active") return source.active;
  if (filter === "dedicated") return source.dedicated;
  if (filter === "inherited") return !source.dedicated && source.targets.length > 0;
  if (filter === "unlinked") return source.targets.length === 0;
  return true;
}

function topologyMatchesQuery(source, query) {
  const normalizedQuery = String(query || "").trim().toLowerCase();
  if (!normalizedQuery) return true;
  const haystack = [
    source.label,
    source.meta,
    ...source.targets.flatMap((target) => [target, targetLabel(target)]),
  ].join(" ").toLowerCase();
  return haystack.includes(normalizedQuery);
}

function topologySummary(sourceItems, allSourceItems, filter, query) {
  const activeCount = allSourceItems.filter((source) => source.active).length;
  const dedicatedCount = allSourceItems.filter((source) => source.dedicated).length;
  const inheritedCount = allSourceItems.filter((source) => !source.dedicated && source.targets.length > 0).length;
  const unlinkedCount = allSourceItems.filter((source) => source.targets.length === 0).length;
  const filterLabel = {
    all: "全部",
    active: "活跃",
    dedicated: "专属",
    inherited: "继承",
    unlinked: "未连接",
  }[filter] || "全部";
  return `
    <div class="topology-statusbar">
      <span>当前 ${sourceItems.length} / ${allSourceItems.length} 个频道</span>
      <span>${activeCount} 活跃</span>
      <span>${dedicatedCount} 专属</span>
      <span>${inheritedCount} 继承</span>
      <span>${unlinkedCount} 未连接</span>
      ${(filter !== "all" || query) ? `<strong>${escapeHtml(filterLabel)}${query ? ` · ${escapeHtml(query)}` : ""}</strong>` : ""}
    </div>
  `;
}

function configuredChannelRefs() {
  const channels = Array.isArray(store.state.config?.source_channels) ? store.state.config.source_channels : [];
  return new Set(channels.map((channel) => normalizeChannelRef(channel?.channel_username)).filter(Boolean));
}

function addTopologyChannel(ref) {
  const channelRef = normalizeChannelRef(ref);
  if (!channelRef) return;
  if (!store.state.config) store.state.config = {};
  if (!Array.isArray(store.state.config.source_channels)) store.state.config.source_channels = [];
  if (configuredChannelRefs().has(channelRef)) {
    showToast("该频道已经在转发配置中。");
    return;
  }
  const channel = { ...defaultChannel(), channel_username: channelRef };
  store.state.config.source_channels.push(channel);
  store.state.expandedChannels.add(channelKey(channel, store.state.config.source_channels.length - 1));
  renderTopologySurfaces();
  showToast("频道已加入转发拓扑，保存后生效。");
}

function connectTopologyTarget(channelIndex, target) {
  const targetValue = String(target || "").trim();
  if (!targetValue) return;
  const channels = store.state.config?.source_channels;
  if (!Array.isArray(channels) || !channels[channelIndex]) return;
  const current = uniqueList(splitList(channels[channelIndex].target_qq_sessions || []));
  const inherited = uniqueList(splitList(store.state.config?.target_qq_session || []));
  const baseTargets = current.length ? current : inherited;
  if (baseTargets.includes(targetValue)) {
    showToast("该频道已经连接到这个 QQ 群。");
    return;
  }
  channels[channelIndex].target_qq_sessions = [...baseTargets, targetValue];
  renderTopologySurfaces();
  showToast("已为频道添加专属 QQ 目标，保存后生效。");
}

function removeTopologyChannel(channelIndex) {
  const channels = store.state.config?.source_channels;
  if (!Array.isArray(channels) || !channels[channelIndex]) return;
  const channel = channels[channelIndex];
  const key = channelKey(channel, channelIndex);
  channels.splice(channelIndex, 1);
  store.state.expandedChannels.delete(key);
  delete store.state.channelGroups[key];
  renderChannels();
  renderTopologySurfaces();
  showToast("已移除频道节点，保存后生效。");
}

function removeTopologyTarget(target) {
  const targetValue = String(target || "").trim();
  if (!targetValue || !store.state.config) return;
  let removed = 0;
  const removeTarget = (items) => {
    const current = uniqueList(splitList(items || []));
    const next = current.filter((item) => item !== targetValue);
    removed += current.length - next.length;
    return next;
  };

  store.state.config.target_qq_session = removeTarget(store.state.config.target_qq_session || []);
  const channels = Array.isArray(store.state.config.source_channels) ? store.state.config.source_channels : [];
  channels.forEach((channel) => {
    channel.target_qq_sessions = removeTarget(channel.target_qq_sessions || []);
  });
  if (!removed) return;
  if (els.targetQQInput) {
    els.targetQQInput.value = joinList(store.state.config.target_qq_session);
  }
  renderQQTargetSelector({
    root: els.defaultQQSelector,
    manualInput: els.targetQQInput,
    inheritLabel: "未配置默认 QQ 目标",
    compact: true,
  });
  renderChannels();
  renderTopologySurfaces();
  showToast("已移除 QQ 群节点，保存后生效。");
}

function closeTopologyContextMenu() {
  if (topologyContextCleanup) {
    topologyContextCleanup();
    topologyContextCleanup = null;
  }
  if (topologyContextMenu) {
    topologyContextMenu.remove();
    topologyContextMenu = null;
  }
}

function showTopologyContextMenu(event, anchorNode, items) {
  closeTopologyContextMenu();
  const menu = document.createElement("div");
  menu.className = "topology-context-menu";
  if (items.length === 1 && items[0]?.danger) {
    menu.classList.add("danger-only");
  }
  menu.setAttribute("role", "menu");
  menu.style.left = `${event.clientX}px`;
  menu.style.top = `${event.clientY}px`;
  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "menuitem");
    button.textContent = item.label;
    if (item.danger) button.classList.add("danger");
    button.addEventListener("click", () => {
      closeTopologyContextMenu();
      item.action();
    });
    menu.appendChild(button);
  });
  document.body.appendChild(menu);
  topologyContextMenu = menu;
  window.requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(event.clientX, window.innerWidth - rect.width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(event.clientY, window.innerHeight - rect.height - 8))}px`;
  });

  const onPointerDown = (nextEvent) => {
    if (!menu.contains(nextEvent.target)) closeTopologyContextMenu();
  };
  const onPointerMove = (nextEvent) => {
    const target = nextEvent.target;
    if (anchorNode.contains(target) || menu.contains(target)) return;
    closeTopologyContextMenu();
  };
  const onKeyDown = (nextEvent) => {
    if (nextEvent.key === "Escape") closeTopologyContextMenu();
  };
  const onScroll = () => closeTopologyContextMenu();
  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("keydown", onKeyDown);
  window.addEventListener("scroll", onScroll, true);
  topologyContextCleanup = () => {
    document.removeEventListener("pointerdown", onPointerDown);
    document.removeEventListener("pointermove", onPointerMove);
    document.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("scroll", onScroll, true);
  };
}

function openTopologyChannel(channelIndex) {
  const channel = store.state.config?.source_channels?.[channelIndex];
  if (!channel) return;
  const key = channelKey(channel, channelIndex);
  store.state.expandedChannels.add(key);
  store.state.channelGroups[key] = "targets";
  renderChannels();
  if (openSectionHandler) openSectionHandler("channels");
  window.requestAnimationFrame(() => {
    document.querySelector(`[data-channel-index="${channelIndex}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  });
}

function bindTargetTopologyInteractions(root, { editable = true, allowOpenChannel = true } = {}) {
  if (!root) return;
  if (editable) {
    root.querySelectorAll("[data-drag-payload]").forEach((node) => {
      node.addEventListener("dragstart", (event) => {
        event.dataTransfer.effectAllowed = "copy";
        event.dataTransfer.setData("application/json", node.dataset.dragPayload || "");
      });
    });

    root.querySelectorAll("[data-topology-add-channel]").forEach((node) => {
      node.addEventListener("click", () => addTopologyChannel(node.dataset.topologyAddChannel));
    });
  }

  root.querySelectorAll("[data-topology-channel]").forEach((node) => {
    const index = Number.parseInt(node.dataset.topologyChannel, 10);
    if (allowOpenChannel) {
      node.addEventListener("click", () => openTopologyChannel(index));
    }
    if (editable) {
      node.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        event.stopPropagation();
        showTopologyContextMenu(event, node, [
          { label: "移除频道节点", danger: true, action: () => removeTopologyChannel(index) },
        ]);
      });
      node.addEventListener("dragover", (event) => {
        event.preventDefault();
        node.classList.add("drop-ready");
      });
      node.addEventListener("dragleave", () => node.classList.remove("drop-ready"));
      node.addEventListener("drop", (event) => {
        event.preventDefault();
        event.stopPropagation();
        node.classList.remove("drop-ready");
        try {
          const payload = JSON.parse(event.dataTransfer.getData("application/json") || "{}");
          if (payload.type === "qq") connectTopologyTarget(index, payload.value);
        } catch {
          showToast("拖拽数据无效。");
        }
      });
    }
  });

  if (editable) {
    root.querySelectorAll("[data-topology-target-key]").forEach((node) => {
      node.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        event.stopPropagation();
        showTopologyContextMenu(event, node, [
          { label: "移除 QQ 群节点", danger: true, action: () => removeTopologyTarget(node.dataset.topologyTargetKey) },
        ]);
      });
    });
  }

  const stage = root.querySelector("[data-topology-drop-stage]");
  if (editable && stage) {
    stage.addEventListener("dragover", (event) => {
      event.preventDefault();
      stage.classList.add("drop-ready");
    });
    stage.addEventListener("dragleave", () => stage.classList.remove("drop-ready"));
    stage.addEventListener("drop", (event) => {
      event.preventDefault();
      stage.classList.remove("drop-ready");
      try {
        const payload = JSON.parse(event.dataTransfer.getData("application/json") || "{}");
        if (payload.type === "tg") addTopologyChannel(payload.value);
        if (payload.type === "qq") showToast("请将 QQ 群拖到左侧 Telegram 频道节点上建立专属关系。");
      } catch {
        showToast("拖拽数据无效。");
      }
    });
  }

  // hover 聚焦：悬停 source/target 节点时，高亮关联边与端点，淡化无关节点，
  // 避免边数多时全局噪声。鼠标离开自动恢复。
  const focusStage = root.querySelector("[data-topology-drop-stage]");
  const focusLines = root.querySelector(".topology-lines");
  if (focusStage && focusLines) {
    const focusSelector = "[data-topology-row], [data-topology-target-row]";
    let clearFocusFrame = 0;
    const rememberPointer = (event) => {
      topologyPointer.x = event.clientX;
      topologyPointer.y = event.clientY;
    };
    const forgetPointer = () => {
      topologyPointer.x = null;
      topologyPointer.y = null;
    };
    const cancelPendingClear = () => {
      if (!clearFocusFrame) return;
      window.cancelAnimationFrame(clearFocusFrame);
      clearFocusFrame = 0;
    };
    const resetFocusClasses = () => {
      focusLines.classList.remove("has-focus");
      focusStage.classList.remove("has-focus");
      focusLines.querySelectorAll(".edge-focus").forEach((p) => {
        p.classList.remove("edge-focus");
        if (p.classList.contains("active")) {
          const now = window.performance?.now?.() || 0;
          p.style.animationDelay = `-${Math.round(now % 1100)}ms, -${Math.round(now % 2400)}ms`;
        } else {
          p.style.animationDelay = "";
        }
      });
      focusStage.querySelectorAll(".node-focus").forEach((n) => n.classList.remove("node-focus"));
    };
    const clearFocus = () => {
      cancelPendingClear();
      resetFocusClasses();
      delete root.dataset.topologyFocusAttr;
      delete root.dataset.topologyFocusValue;
    };
    const nearestFocusNode = (target) => target instanceof Element ? target.closest(focusSelector) : null;
    const focusNodeAtPointer = () => {
      if (topologyPointer.x == null || topologyPointer.y == null) return null;
      const currentStage = root.querySelector("[data-topology-drop-stage]");
      const node = nearestFocusNode(document.elementFromPoint(topologyPointer.x, topologyPointer.y));
      return node && currentStage?.contains(node) ? node : null;
    };
    const scheduleClearFocus = () => {
      cancelPendingClear();
      clearFocusFrame = window.requestAnimationFrame(() => {
        clearFocusFrame = 0;
        if (!focusStage.isConnected) return;
        if (focusNodeAtPointer()) return;
        clearFocus();
      });
    };
    const focusByEdgeAttr = (attr, value) => {
      cancelPendingClear();
      resetFocusClasses();
      const edges = focusLines.querySelectorAll(`path[${attr}="${value}"]`);
      if (!edges.length) {
        delete root.dataset.topologyFocusAttr;
        delete root.dataset.topologyFocusValue;
        return;
      }
      root.dataset.topologyFocusAttr = attr;
      root.dataset.topologyFocusValue = value;
      focusLines.classList.add("has-focus");
      focusStage.classList.add("has-focus");
      const sourceRows = new Set();
      const targetRows = new Set();
      const now = window.performance?.now?.() || 0;
      const animationDelay = `-${Math.round(now % 1100)}ms, -${Math.round(now % 2400)}ms`;
      edges.forEach((p) => {
        p.style.animationDelay = animationDelay;
        p.classList.add("edge-focus");
        sourceRows.add(p.dataset.source);
        targetRows.add(p.dataset.target);
      });
      sourceRows.forEach((r) => focusStage.querySelector(`[data-topology-row="${r}"]`)?.classList.add("node-focus"));
      targetRows.forEach((r) => focusStage.querySelector(`[data-topology-target-row="${r}"]`)?.classList.add("node-focus"));
    };
    const focusNode = (node) => {
      if (!node) return;
      if (node.dataset.topologyRow != null) {
        focusByEdgeAttr("data-source", node.dataset.topologyRow);
      } else if (node.dataset.topologyTargetRow != null) {
        focusByEdgeAttr("data-target", node.dataset.topologyTargetRow);
      }
    };
    const restoreFocus = () => {
      const attr = root.dataset.topologyFocusAttr;
      const value = root.dataset.topologyFocusValue;
      if (attr && value != null) {
        focusByEdgeAttr(attr, value);
      } else {
        const node = focusNodeAtPointer();
        if (node) {
          focusNode(node);
        } else {
          clearFocus();
        }
      }
    };
    focusStage.addEventListener("pointerover", (event) => {
      if (!focusStage.isConnected) return;
      rememberPointer(event);
      const node = nearestFocusNode(event.target);
      if (!node || nearestFocusNode(event.relatedTarget) === node) return;
      focusNode(node);
    });
    focusStage.addEventListener("pointermove", (event) => {
      if (!focusStage.isConnected) return;
      rememberPointer(event);
    });
    focusStage.addEventListener("pointerout", (event) => {
      if (!focusStage.isConnected) return;
      if (event.target instanceof Element && !event.target.isConnected) return;
      rememberPointer(event);
      const fromNode = nearestFocusNode(event.target);
      const toNode = nearestFocusNode(event.relatedTarget);
      if (!fromNode || fromNode === toNode) return;
      if (toNode && focusStage.contains(toNode)) {
        focusNode(toNode);
        return;
      }
      scheduleClearFocus();
    });
    focusStage.addEventListener("pointerleave", (event) => {
      if (!focusStage.isConnected) return;
      forgetPointer();
      clearFocus();
    });
    focusStage.addEventListener("focusin", (event) => {
      if (!focusStage.isConnected) return;
      focusNode(nearestFocusNode(event.target));
    });
    focusStage.addEventListener("focusout", (event) => {
      if (!focusStage.isConnected) return;
      if (event.target instanceof Element && !event.target.isConnected) return;
      const fromNode = nearestFocusNode(event.target);
      const nextNode = nearestFocusNode(event.relatedTarget);
      if (!fromNode || fromNode === nextNode) return;
      if (nextNode && focusStage.contains(nextNode)) {
        focusNode(nextNode);
        return;
      }
      clearFocus();
    });
    if (root.dataset.topologyFocusAttr && root.dataset.topologyFocusValue != null) {
      restoreFocus();
    }
  }

  // Palette search inputs
  const channelsSearch = root.querySelector("[data-topology-search-channels]");
  if (channelsSearch) {
    bindLiveSearchInput(channelsSearch, (value) => {
      topologyUiState.paletteChannelsQuery = value;
      renderTargetTopology();
    });
  }

  const groupsSearch = root.querySelector("[data-topology-search-groups]");
  if (groupsSearch) {
    bindLiveSearchInput(groupsSearch, (value) => {
      topologyUiState.paletteGroupsQuery = value;
      renderTargetTopology();
    });
  }
}

function captureTopologyScroll(root) {
  if (!root) return {};
  const scrollState = {};
  root.querySelectorAll("[data-topology-scroll]").forEach((node) => {
    scrollState[node.dataset.topologyScroll] = {
      left: node.scrollLeft,
      top: node.scrollTop,
    };
  });
  return scrollState;
}

function restoreTopologyScroll(root, scrollState) {
  if (!root || !scrollState) return;
  root.querySelectorAll("[data-topology-scroll]").forEach((node) => {
    const state = scrollState[node.dataset.topologyScroll];
    if (!state) return;
    node.scrollLeft = state.left;
    node.scrollTop = state.top;
  });
}

function renderTopologyInto(root, {
  showPalette = true,
  editable = true,
  allowOpenChannel = true,
  emptyMessage = "还没有配置源频道。把上方 Telegram 频道拖到这里开始配置。",
  markerId = "topology-arrow",
  minStageHeight = 0,
  query = "",
  filter = "all",
  showSummary = false,
} = {}) {
  if (!root) return;
  closeTopologyContextMenu();
  // Capture active search input and selection range
  let activeSearchType = null;
  let selectionStart = 0;
  let selectionEnd = 0;
  if (document.activeElement && root.contains(document.activeElement)) {
    if (document.activeElement.hasAttribute("data-topology-search-channels")) {
      activeSearchType = "channels";
    } else if (document.activeElement.hasAttribute("data-topology-search-groups")) {
      activeSearchType = "groups";
    }
    if (activeSearchType) {
      selectionStart = document.activeElement.selectionStart || 0;
      selectionEnd = document.activeElement.selectionEnd || 0;
    }
  }

  const scrollState = captureTopologyScroll(root);
  const cfg = store.state.config || {};
  const channels = Array.isArray(cfg.source_channels) ? cfg.source_channels : [];
  const defaultTargets = uniqueList(splitList(cfg.target_qq_session || []));
  const safeFilter = TOPOLOGY_FILTERS.has(filter) ? filter : "all";

  // Calculate a signature to check if the topology data has actually changed.
  // If nothing changed, we skip innerHTML re-render to prevent interrupting running GSAP animations.
  const dataKey = JSON.stringify({
    channels,
    defaultTargets,
    safeFilter,
    query,
    paletteChannelsQuery: topologyUiState.paletteChannelsQuery,
    paletteGroupsQuery: topologyUiState.paletteGroupsQuery,
    queue: store.state.status?.queue?.by_channel,
    tgChannels: store.state.tgChannels,
    qqGroups: store.state.qqGroups
  });

  if (root.dataset.lastTopologyKey === dataKey) {
    return;
  }
  root.dataset.lastTopologyKey = dataKey;
  const allSourceItems = channels
    .map((channel, index) => {
      const username = normalizeChannelRef(channel?.channel_username || "");
      const dedicatedTargets = uniqueList(splitList(channel?.target_qq_sessions || []));
      const targets = dedicatedTargets.length ? dedicatedTargets : defaultTargets;
      const pendingCount = queueCountForChannel(channel);
      return {
        id: `source-${index}`,
        index,
        label: username ? channelTitleUI(username) : `频道 ${index + 1}`,
        meta: pendingCount > 0
          ? `${pendingCount} 条待转发`
          : dedicatedTargets.length ? "专属 QQ 目标" : targets.length ? "继承默认 QQ 目标" : "未连接 QQ 群",
        targets,
        dedicated: dedicatedTargets.length > 0,
        active: pendingCount > 0,
      };
    })
    .filter((item) => item.label || item.targets.length);
  const sourceItems = topologySortSources(
    allSourceItems.filter((source) => topologyMatchesFilter(source, safeFilter) && topologyMatchesQuery(source, query)),
  );

  const targetItems = [];
  const targetIndex = new Map();
  sourceItems.forEach((source) => {
    source.targets.forEach((target) => {
      const key = String(target || "").trim();
      if (!key) return;
      let position = targetIndex.get(key);
      if (position == null) {
        position = targetItems.length;
        targetIndex.set(key, position);
        targetItems.push({
          id: `target-${position}`,
          key,
          label: targetLabel(key),
          meta: groupByTarget(key)?.source || "configured",
          inDegree: 0,
        });
      }
      targetItems[position].inDegree += 1;
    });
  });
  const targetYs = topologyTargetYLayout(sourceItems, targetIndex, targetItems.length);

  const configuredRefs = configuredChannelRefs();
  const availableChannels = store.state.tgChannels
    .filter((channel) => {
      const ref = normalizeChannelRef(channel.channel_ref || channel.username);
      return ref && !configuredRefs.has(ref);
    });
  const availableGroups = store.state.qqGroups;

  // Filter channels: 首位 @ 时同时匹配带 @ / 去 @（TG username 常无 @）
  const channelsKeyword = String(topologyUiState.paletteChannelsQuery || "").trim();
  const filteredChannels = availableChannels.filter((channel) =>
    fieldsMatchSearch(
      [
        channel.title,
        channel.username,
        channel.channel_ref,
        channel.username ? `@${channel.username}` : "",
      ],
      channelsKeyword,
    ),
  );

  // Filter groups: 首位 @ 保留 @ 相关匹配，并兼容去 @
  const groupsKeyword = String(topologyUiState.paletteGroupsQuery || "").trim();
  const filteredGroups = availableGroups.filter((group) =>
    fieldsMatchSearch(
      [group.group_id, group.group_name, group.group_name ? `@${group.group_name}` : ""],
      groupsKeyword,
    ),
  );

  const palette = showPalette ? `
    <div class="topology-palette">
      <div class="topology-palette-col">
        <strong>可拖入频道</strong>
        <div class="topology-palette-search-wrapper">
          <input data-topology-search-channels type="search" placeholder="搜索频道标题、用户名或 ID" value="${escapeHtml(topologyUiState.paletteChannelsQuery)}" />
        </div>
        <div class="topology-palette-status">${filteredChannels.length} 个可拖入频道</div>
        <div class="topology-chip-row" data-topology-scroll="channels">
          ${
            filteredChannels.length
              ? filteredChannels.map((channel) => {
                  const ref = normalizeChannelRef(channel.channel_ref || channel.username);
                  const label = channel.title || channel.username || ref;
                  const meta = channel.username ? `@${channel.username}` : ref;
                  return renderSelectorChip({
                    label,
                    meta,
                    className: "topology-chip",
                    attrs: `draggable="true" data-topology-add-channel="${escapeHtml(ref)}" data-drag-payload="${topologyDragData("tg", ref)}"`,
                  });
                }).join("")
              : '<span class="topology-palette-empty">没有匹配的 Telegram 频道</span>'
          }
        </div>
      </div>
      <div class="topology-palette-col">
        <strong>可拖入 QQ 群</strong>
        <div class="topology-palette-search-wrapper">
          <input data-topology-search-groups type="search" placeholder="搜索 QQ 群名或群号" value="${escapeHtml(topologyUiState.paletteGroupsQuery)}" />
        </div>
        <div class="topology-palette-status">${filteredGroups.length} 个可拖入 QQ 群</div>
        <div class="topology-chip-row" data-topology-scroll="groups">
          ${
            filteredGroups.length
              ? filteredGroups.map((group) => {
                  const target = String(group.session || `default:GroupMessage:${group.group_id || ""}`).trim();
                  const label = group.group_name || `群 ${group.group_id}`;
                  const meta = group.group_id ? String(group.group_id) : group.source || "live";
                  return renderSelectorChip({
                    label,
                    meta,
                    className: "topology-chip",
                    attrs: `draggable="true" data-drag-payload="${topologyDragData("qq", target)}"`,
                  });
                }).join("")
              : '<span class="topology-palette-empty">没有匹配的 QQ 群</span>'
          }
        </div>
      </div>
    </div>
  ` : "";

  if (!sourceItems.length) {
    root.innerHTML = `
      ${showSummary ? topologySummary(sourceItems, allSourceItems, safeFilter, query) : ""}
      ${palette}
      <div class="topology-empty" data-topology-drop-stage>${escapeHtml(allSourceItems.length ? "没有匹配当前筛选条件的转发关系。" : emptyMessage)}</div>
    `;
    bindTargetTopologyInteractions(root, { editable, allowOpenChannel });
    restoreTopologyScroll(root, scrollState);

    // Restore active search input focus and cursor position
    if (activeSearchType) {
      const input = root.querySelector(activeSearchType === "channels" ? "[data-topology-search-channels]" : "[data-topology-search-groups]");
      if (input) {
        input.focus();
        try {
          input.setSelectionRange(selectionStart, selectionEnd);
        } catch (e) {}
      }
    }
    return;
  }

  const maxTargetY = targetYs.length ? Math.max(...targetYs) : TOPOLOGY_TOP_PADDING;
  const stageHeight = Math.max(
    TOPOLOGY_TOP_PADDING + Math.max(sourceItems.length - 1, 1) * TOPOLOGY_ROW_HEIGHT + TOPOLOGY_BOTTOM_PADDING,
    maxTargetY + TOPOLOGY_BOTTOM_PADDING,
    minStageHeight,
  );
  const sourceNodes = sourceItems
    .map((source, index) =>
      topologyNode(
        source.label,
        source.meta,
        `source ${source.active ? "active" : ""}`,
        index,
        `data-topology-channel="${source.index}" data-topology-row="${index}"`,
      ),
    )
    .join("");
  const targetNodes = targetItems.length
    ? targetItems.map((target, index) =>
        topologyNode(
        target.label,
        target.meta,
        "target",
        index,
        `${editable ? `draggable="true" data-drag-payload="${topologyDragData("qq", target.key)}"` : ""} data-topology-target-row="${index}" data-topology-target-key="${escapeHtml(target.key)}"`,
        target.inDegree > 1 ? `${target.inDegree} 源` : "",
        targetYs[index],
      ),
    ).join("")
    : '<div class="topology-empty topology-empty-target">未选择 QQ 群目标</div>';

  const now = window.performance?.now?.() || 0;
  const activeDelay = `-${Math.round(now % 1100)}ms, -${Math.round(now % 2400)}ms`;

  const edges = sourceItems
    .flatMap((source, sourceIndex) =>
      source.targets.map((target) => {
        const targetPosition = targetIndex.get(String(target || "").trim());
        if (targetPosition == null) return "";
        const y1 = topologyY(sourceIndex);
        const y2 = targetYs[targetPosition];
        const edgeClass = `${source.dedicated ? "dedicated" : "inherited"} ${source.active ? "active" : ""}`;
        const style = source.active ? `style="animation-delay: ${activeDelay}"` : "";
        return `<path class="${edgeClass}" ${style} data-source="${sourceIndex}" data-target="${targetPosition}" d="M 0 ${y1} C 35 ${y1}, 65 ${y2}, 100 ${y2}" />`;
      }),
    )
    .join("");

  const mobileRows = sourceItems
    .map((source) => {
      const chips = source.targets.length
        ? source.targets.map((target) => `<span>${escapeHtml(targetLabel(target))}</span>`).join("")
        : '<em>未连接 QQ 群</em>';
      return `
        <div class="topology-mobile-row">
          <button type="button" data-topology-channel="${source.index}">${escapeHtml(source.label)}</button>
          <div>${chips}</div>
        </div>
      `;
    })
    .join("");

  root.innerHTML = `
    ${showSummary ? topologySummary(sourceItems, allSourceItems, safeFilter, query) : ""}
    ${palette}
    <div class="topology-canvas" data-topology-scroll="canvas">
      <div class="topology-stage" data-topology-drop-stage style="--topology-height: ${stageHeight}px">
        <span class="topology-col-label">Telegram 频道</span>
        <span class="topology-col-label topology-col-label-right">QQ 目标</span>
        <svg class="topology-lines" viewBox="0 0 100 ${stageHeight}" preserveAspectRatio="none" aria-hidden="true">
          ${edges}
        </svg>
        ${sourceNodes}
        ${targetNodes}
      </div>
    </div>
    <div class="topology-mobile-list">${mobileRows}</div>
  `;
  bindTargetTopologyInteractions(root, { editable, allowOpenChannel });
  restoreTopologyScroll(root, scrollState);

  // Restore active search input focus and cursor position
  if (activeSearchType) {
    const input = root.querySelector(activeSearchType === "channels" ? "[data-topology-search-channels]" : "[data-topology-search-groups]");
    if (input) {
      input.focus();
      try {
        input.setSelectionRange(selectionStart, selectionEnd);
      } catch (e) {}
    }
  }

  // Staggered entrance animation for nodes and self-drawing lines using GSAP
  const isTyping = document.activeElement &&
    (document.activeElement === root.querySelector("[data-topology-search-channels]") ||
     document.activeElement === root.querySelector("[data-topology-search-groups]") ||
     document.activeElement === els.topologySearchInput);

  animateTopologyInto(root, { isTyping: Boolean(isTyping) });
}

export function animateTopologyInto(root, { isTyping = false } = {}) {
  if (!root || !window.gsap || !motionEnabled()) return;

  if (isTyping) {
    window.gsap.killTweensOf(root.querySelectorAll(".topology-node"));
    window.gsap.fromTo(root.querySelectorAll(".topology-node"),
      { opacity: 0.5 },
      { opacity: 1, duration: 0.15, ease: "power2.out" }
    );
    return;
  }

  // Staggered entrance for nodes
  const sources = root.querySelectorAll(".topology-node-source");
  const targets = root.querySelectorAll(".topology-node-target");

  if (sources.length) {
    window.gsap.killTweensOf(sources);
    window.gsap.fromTo(sources,
      { opacity: 0, x: -15 },
      { opacity: 1, x: 0, duration: 0.4, stagger: 0.03, ease: "power2.out" }
    );
  }
  if (targets.length) {
    window.gsap.killTweensOf(targets);
    window.gsap.fromTo(targets,
      { opacity: 0, x: 15 },
      { opacity: 1, x: 0, duration: 0.4, stagger: 0.03, ease: "power2.out" }
    );
  }
}

export function renderTargetTopology() {
  renderTopologyInto(els.targetTopology, {
    showPalette: true,
    editable: true,
    allowOpenChannel: true,
    markerId: "target-topology-arrow",
    minStageHeight: 520,
    query: topologyUiState.query,
    filter: topologyUiState.filter,
    showSummary: true,
  });
}

export function renderOverviewTopology() {
  renderTopologyInto(els.overviewTopology, {
    showPalette: false,
    editable: false,
    allowOpenChannel: true,
    emptyMessage: "还没有配置源频道。请在目标配置中建立频道到 QQ 群的转发关系。",
    markerId: "overview-topology-arrow",
  });
}

export function renderTopologySurfaces() {
  renderTargetTopology();
  renderOverviewTopology();
}

export function syncTopologyControls() {
  if (els.topologySearchInput && els.topologySearchInput.value !== topologyUiState.query) {
    els.topologySearchInput.value = topologyUiState.query;
  }
  els.topologyFilterGroup?.querySelectorAll("[data-topology-filter]").forEach((button) => {
    const isActive = button.dataset.topologyFilter === topologyUiState.filter;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}
