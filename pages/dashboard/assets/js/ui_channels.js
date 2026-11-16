import { store } from './store.js';
import { escapeHtml, channelKey } from './utils.js';
import { renderQQTargetSelector, renderTGChannelSelector, channelTitleUI, splitList, joinList } from './ui_selector.js';
import { els } from './context.js';
import { MSG_TYPES, TRI_STATE, CHANNEL_GROUPS } from './config.js';

export function defaultChannel() {
  return {
    __template_key: "default",
    channel_username: "",
    start_time: "",
    check_interval: 0,
    msg_limit: 10,
    priority: 0,
    exclude_text_on_media: "继承全局",
    forward_types: MSG_TYPES.slice(),
    max_file_size: 0,
    ignore_global_filters: false,
    filter_keywords: [],
    filter_regex: "",
    filter_spoiler_messages: "继承全局",
    filter_qr_code_images: "继承全局",
    strip_markdown_links: "继承全局",
    monitor_keywords: [],
    monitor_regex: "",
    target_qq_sessions: [],
  };
}

function triStateSelect(name, value) {
  return `
    <select data-channel-field="${name}">
      ${TRI_STATE.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
    </select>
  `;
}

function activeChannelGroup(key) {
  return store.state.channelGroups[key] || CHANNEL_GROUPS[0].id;
}

export function channelField(card, name) {
  return card.querySelector(`[data-channel-field="${name}"]`);
}

function normalizeChannelFieldValue(field, fallback) {
  if (!field) return String(fallback || "").replace(/^[@#]/, "");
  return String(field.value || "").trim().replace(/^[@#]/, "");
}

export function collectChannels({ keepEmpty = true } = {}) {
  const cards = Array.from(document.querySelectorAll(".channel-card[data-channel-index]"));
  if (!cards.length) {
    if (!Array.isArray(store.state.config.source_channels)) {
      store.state.config.source_channels = [];
    }
    return;
  }
  store.state.config.source_channels = cards
    .map((card) => {
      const index = Number.parseInt(card.dataset.channelIndex, 10);
      const current = { ...defaultChannel(), ...(store.state.config.source_channels?.[index] || {}) };
      const getText = (name) => channelField(card, name)?.value?.trim() || "";
      const getInt = (name, fallback) => {
        const raw = channelField(card, name)?.value;
        if (raw == null) return fallback;
        const parsed = Number.parseInt(String(raw).trim(), 10);
        return Number.isFinite(parsed) ? parsed : fallback;
      };
      const getFloat = (name, fallback) => {
        const raw = channelField(card, name)?.value;
        if (raw == null) return fallback;
        const parsed = Number.parseFloat(String(raw).trim());
        return Number.isFinite(parsed) ? parsed : fallback;
      };
      const getList = (name, fallback) => {
        const field = channelField(card, name);
        if (!field) return fallback;
        return splitList(field.value);
      };
      const getChecks = (name, fallback) => {
        const field = channelField(card, name);
        if (!field) return fallback;
        return Array.from(field.querySelectorAll("input:checked")).map((item) => item.value);
      };
      const getBool = (name, fallback) => {
        const field = channelField(card, name);
        return field ? Boolean(field.checked) : fallback;
      };
      const getTri = (name, fallback) => getText(name) || fallback;
      const oldKey = card.dataset.channelKey;
      const channelUsernameField = channelField(card, "channel_username");
      const collected = {
        __template_key: "default",
        channel_username: normalizeChannelFieldValue(
          channelUsernameField,
          current.channel_username,
        ),
        start_time: channelField(card, "start_time") ? getText("start_time") : current.start_time,
        check_interval: getInt("check_interval", current.check_interval),
        msg_limit: getInt("msg_limit", current.msg_limit),
        priority: getInt("priority", current.priority),
        exclude_text_on_media: getTri("exclude_text_on_media", current.exclude_text_on_media),
        forward_types: getChecks("forward_types", current.forward_types),
        max_file_size: getFloat("max_file_size", current.max_file_size),
        ignore_global_filters: getBool("ignore_global_filters", current.ignore_global_filters),
        filter_keywords: getList("filter_keywords", current.filter_keywords),
        filter_regex: channelField(card, "filter_regex") ? getText("filter_regex") : current.filter_regex,
        filter_spoiler_messages: getTri("filter_spoiler_messages", current.filter_spoiler_messages),
        filter_qr_code_images: getTri("filter_qr_code_images", current.filter_qr_code_images),
        strip_markdown_links: getTri("strip_markdown_links", current.strip_markdown_links),
        monitor_keywords: getList("monitor_keywords", current.monitor_keywords),
        monitor_regex: channelField(card, "monitor_regex") ? getText("monitor_regex") : current.monitor_regex,
        target_qq_sessions: getList("target_qq_sessions", current.target_qq_sessions),
      };
      const newKey = channelKey(collected, index);
      if (oldKey && newKey !== oldKey) {
        if (store.state.expandedChannels.has(oldKey)) {
          store.state.expandedChannels.delete(oldKey);
          store.state.expandedChannels.add(newKey);
        }
        if (store.state.channelGroups[oldKey]) {
          store.state.channelGroups[newKey] = store.state.channelGroups[oldKey];
          delete store.state.channelGroups[oldKey];
        }
      }
      return collected;
    })
    .filter((channel) => keepEmpty || channel.channel_username);
}

export function renderChannels() {
  const channels = Array.isArray(store.state.config?.source_channels) ? store.state.config.source_channels : [];
  if (!els.channelList) return;
  els.channelList.innerHTML = channels.length
    ? channels
        .map((channel, index) => {
          const cfg = { ...defaultChannel(), ...channel };
          const key = channelKey(cfg, index);
          const collapsed = !store.state.expandedChannels.has(key);
          const selectedTypes = Array.isArray(cfg.forward_types) ? cfg.forward_types : [];
          const title = cfg.channel_username ? channelTitleUI(cfg.channel_username) : "新频道";
          const group = activeChannelGroup(key);
          const targetSummary = cfg.target_qq_sessions?.length
            ? `专属 ${cfg.target_qq_sessions.length} 个 QQ 目标`
            : store.state.config?.target_qq_session?.length
              ? "继承默认 QQ 目标"
              : "未配置 QQ 目标";
          return `
            <article class="channel-card ${collapsed ? "collapsed" : ""}" data-channel-index="${index}" data-channel-key="${escapeHtml(key)}">
              <button class="channel-summary" type="button" data-toggle-channel="${index}">
                <div>
                  <div class="channel-title-text">${escapeHtml(title)}</div>
                  <div class="channel-meta">
                    <span>抓取 ${escapeHtml(cfg.msg_limit)} 条</span>
                    <span>间隔 ${escapeHtml(cfg.check_interval || "全局")} 秒</span>
                    <span>优先级 ${escapeHtml(cfg.priority || 0)}</span>
                    <span>${escapeHtml(targetSummary)}</span>
                  </div>
                </div>
                <span class="chevron">⌄</span>
              </button>
              <div class="channel-body">
                <div class="segmented channel-tabs">
                  ${CHANNEL_GROUPS.map(
                    (item) =>
                      `<button type="button" data-channel-tab="${index}" data-channel-tab-id="${item.id}" class="${group === item.id ? "active" : ""}">${escapeHtml(item.label)}</button>`,
                  ).join("")}
                </div>
                <section class="channel-section ${group === "base" ? "active" : ""}" data-channel-section="base">
                  <div class="selector-host" data-channel-tg-selector="${index}"></div>
                  <div class="form-grid">
                    <label class="field">频道用户名<input data-channel-field="channel_username" value="${escapeHtml(cfg.channel_username)}" /></label>
                    <label class="field">起始日期<input data-channel-field="start_time" value="${escapeHtml(cfg.start_time)}" placeholder="YYYY-MM-DD" /></label>
                    <label class="field">检测间隔<input data-channel-field="check_interval" type="number" value="${escapeHtml(cfg.check_interval)}" /></label>
                    <label class="field">单次抓取上限<input data-channel-field="msg_limit" type="number" value="${escapeHtml(cfg.msg_limit)}" /></label>
                    <label class="field">优先级<input data-channel-field="priority" type="number" value="${escapeHtml(cfg.priority)}" /></label>
                  </div>
                </section>
                <section class="channel-section ${group === "content" ? "active" : ""}" data-channel-section="content">
                  <div class="form-grid">
                    <label class="field">文件大小限制(MB)<input data-channel-field="max_file_size" type="number" step="0.1" value="${escapeHtml(cfg.max_file_size)}" /></label>
                    <label class="field">媒体文本${triStateSelect("exclude_text_on_media", cfg.exclude_text_on_media)}</label>
                    <label class="field">剧透过滤${triStateSelect("filter_spoiler_messages", cfg.filter_spoiler_messages)}</label>
                    <label class="field">Markdown 链接${triStateSelect("strip_markdown_links", cfg.strip_markdown_links)}</label>
                  </div>
                  <div class="check-group" data-channel-field="forward_types">
                    ${MSG_TYPES.map(
                      (type) => `
                        <label class="check-pill">
                          <input type="checkbox" value="${escapeHtml(type)}" ${selectedTypes.includes(type) ? "checked" : ""} />
                          ${escapeHtml(type)}
                        </label>
                      `,
                    ).join("")}
                  </div>
                </section>
                <section class="channel-section ${group === "filters" ? "active" : ""}" data-channel-section="filters">
                  <label class="toggle-row">
                    <input data-channel-field="ignore_global_filters" type="checkbox" ${cfg.ignore_global_filters ? "checked" : ""} />
                    <span>忽略全局文本过滤</span>
                  </label>
                  <div class="form-grid">
                    <label class="field">二维码图片${triStateSelect("filter_qr_code_images", cfg.filter_qr_code_images)}</label>
                    <label class="field">过滤关键词<textarea data-channel-field="filter_keywords" rows="4">${escapeHtml(joinList(cfg.filter_keywords))}</textarea></label>
                    <label class="field">监听关键词<textarea data-channel-field="monitor_keywords" rows="4">${escapeHtml(joinList(cfg.monitor_keywords))}</textarea></label>
                    <label class="field">过滤正则<input data-channel-field="filter_regex" value="${escapeHtml(cfg.filter_regex)}" /></label>
                    <label class="field">监听正则<input data-channel-field="monitor_regex" value="${escapeHtml(cfg.monitor_regex)}" /></label>
                  </div>
                </section>
                <section class="channel-section ${group === "targets" ? "active" : ""}" data-channel-section="targets">
                  <div class="selector-host" data-channel-qq-selector="${index}"></div>
                  <label class="field">手写专属 QQ 目标（每行一个群号或完整会话名）<textarea data-channel-field="target_qq_sessions" rows="4">${escapeHtml(joinList(cfg.target_qq_sessions))}</textarea></label>
                  <button class="btn btn-soft" data-inherit-qq-targets="${index}" type="button">恢复继承默认 QQ 目标</button>
                </section>
                <div class="channel-actions">
                  <button class="btn btn-soft danger" data-remove-channel="${index}" type="button">删除频道</button>
                </div>
              </div>
            </article>
          `;
        })
        .join("")
    : '<div class="queue-item"><span>暂无源频道</span><strong>0</strong></div>';

  document.querySelectorAll(".channel-card[data-channel-index]").forEach((card) => {
    const tgRoot = card.querySelector("[data-channel-tg-selector]");
    const tgInput = channelField(card, "channel_username");
    renderTGChannelSelector({ root: tgRoot, manualInput: tgInput });

    const qqRoot = card.querySelector("[data-channel-qq-selector]");
    const qqInput = channelField(card, "target_qq_sessions");
    renderQQTargetSelector({
      root: qqRoot,
      manualInput: qqInput,
      inheritLabel: "继承默认 QQ 目标",
    });
  });

  document.querySelectorAll("[data-toggle-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      collectChannels();
      const card = button.closest(".channel-card");
      const key = card.dataset.channelKey;
      if (store.state.expandedChannels.has(key)) {
        store.state.expandedChannels.delete(key);
      } else {
        store.state.expandedChannels.add(key);
      }
      renderChannels();
    });
  });
  document.querySelectorAll("[data-channel-tab]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      collectChannels();
      const card = button.closest(".channel-card");
      const key = card.dataset.channelKey;
      store.state.channelGroups[key] = button.dataset.channelTabId;
      store.state.expandedChannels.add(key);
      renderChannels();
    });
  });
  document.querySelectorAll("[data-remove-channel]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const index = Number.parseInt(button.dataset.removeChannel, 10);
      collectChannels();
      store.state.config.source_channels.splice(index, 1);
      renderChannels();
    });
  });
  document.querySelectorAll("[data-inherit-qq-targets]").forEach((button) => {
    button.addEventListener("click", () => {
      const card = button.closest(".channel-card");
      const textarea = channelField(card, "target_qq_sessions");
      if (textarea) textarea.value = "";
      collectChannels();
      renderChannels();
    });
  });
}

export function initChannels() {
  if (els.addChannelBtn) {
    els.addChannelBtn.addEventListener('click', () => {
      collectChannels();
      const channel = defaultChannel();
      if (!store.state.config.source_channels) {
        store.state.config.source_channels = [];
      }
      store.state.config.source_channels.push(channel);
      store.state.expandedChannels.add(channelKey(channel, store.state.config.source_channels.length - 1));
      renderChannels();
    });
  }
}
