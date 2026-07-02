import { store } from './store.js';
import { escapeHtml, channelKey } from './utils.js';
import { renderQQTargetSelector, renderTGChannelSelector, channelTitleUI, splitList, joinList } from './selector.js';
import { els } from './context.js';
import { MSG_TYPES, TRI_STATE, CHANNEL_GROUPS, MERGE_RULE_CLASSES } from '../app.js';

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
      const collected = {
        __template_key: "default",
        channel_username: (getText("channel_username") || current.channel_username).replace(/^[@#]/, ""),
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
                    <label class="field">过滤关键词<textarea data-channel-field="filter_keywords" rows="4">${escapeHtml(joinList(cfg.filter_keywords))}</textarea></label>
                    <label class="field">监听关键词<textarea data-channel-field="monitor_keywords" rows="4">${escapeHtml(joinList(cfg.monitor_keywords))}</textarea></label>
                    <label class="field">过滤正则<input data-channel-field="filter_regex" value="${escapeHtml(cfg.filter_regex)}" /></label>
                    <label class="field">监听正则<input data-channel-field="monitor_regex" value="${escapeHtml(cfg.monitor_regex)}" /></label>
                  </div>
                </section>
                <section class="channel-section ${group === "targets" ? "active" : ""}" data-channel-section="targets">
                  <div class="selector-host" data-channel-qq-selector="${index}"></div>
                  <label class="field">手写专属目标<textarea data-channel-field="target_qq_sessions" rows="4">${escapeHtml(joinList(cfg.target_qq_sessions))}</textarea></label>
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

export function defaultMergeRule() {
  return {
    __template_key: "default",
    name: "",
    channel: "",
    rule_class: "KeywordNextNMerge",
    params: {
      trigger_keywords: [],
      trigger_regex: "",
      next_count: 2,
      time_window_seconds: 60,
    },
  };
}

export function normalizeMergeRule(rule = {}) {
  const defaults = defaultMergeRule();
  return {
    ...defaults,
    ...rule,
    params: {
      ...defaults.params,
      ...(rule.params || {}),
    },
  };
}

export function mergeRuleKey(rule, index) {
  const normalized = normalizeMergeRule(rule);
  return normalized.name || normalized.channel || normalized.rule_class
    ? `rule:${normalized.name}:${normalized.channel}:${normalized.rule_class}:${index}`
    : `idx:${index}`;
}

export function mergeRuleTitle(rule) {
  const normalized = normalizeMergeRule(rule);
  if (normalized.name) {
    return normalized.name;
  }
  const classLabel = MERGE_RULE_CLASSES.find((item) => item.value === normalized.rule_class)?.label || normalized.rule_class || "未选择规则";
  return normalized.channel ? `@${normalized.channel} · ${classLabel}` : `新规则 · ${classLabel}`;
}

export function collectMergeRules({ keepEmpty = true } = {}) {
  const cards = Array.from(document.querySelectorAll(".merge-card[data-merge-index]"));
  if (!cards.length) {
    if (!Array.isArray(store.state.config.merge_rules)) {
      store.state.config.merge_rules = [];
    }
    return;
  }

  store.state.config.merge_rules = cards
    .map((card) => {
      const index = Number.parseInt(card.dataset.mergeIndex, 10);
      const current = normalizeMergeRule(store.state.config.merge_rules?.[index] || {});
      const field = (name) => card.querySelector(`[data-merge-field="${name}"]`);
      const param = (name) => card.querySelector(`[data-merge-param="${name}"]`);
      const getText = (node, fallback = "") => (node ? node.value.trim() : fallback);
      const getInt = (node, fallback) => {
        if (!node) return fallback;
        const parsed = Number.parseInt(String(node.value).trim(), 10);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
      };
      const collected = {
        __template_key: "default",
        name: getText(field("name"), current.name),
        channel: getText(field("channel"), current.channel).replace(/^[@#]/, ""),
        rule_class: getText(field("rule_class"), current.rule_class),
        params: {
          ...current.params,
          trigger_keywords: splitList(param("trigger_keywords")?.value || ""),
          trigger_regex: getText(param("trigger_regex"), current.params.trigger_regex || ""),
          next_count: getInt(param("next_count"), current.params.next_count || 2),
          time_window_seconds: getInt(param("time_window_seconds"), current.params.time_window_seconds || 60),
        },
      };
      const oldKey = card.dataset.mergeKey;
      const newKey = mergeRuleKey(collected, index);
      if (oldKey && newKey !== oldKey && store.state.expandedMergeRules.has(oldKey)) {
        store.state.expandedMergeRules.delete(oldKey);
        store.state.expandedMergeRules.add(newKey);
      }
      return collected;
    })
    .filter((rule) => keepEmpty || (rule.channel && rule.rule_class));
}

export function renderMergeRules() {
  const rules = Array.isArray(store.state.config?.merge_rules) ? store.state.config.merge_rules : [];
  if (!els.mergeRuleList) return;
  els.mergeRuleList.innerHTML = rules.length
    ? rules
        .map((rule, index) => {
          const cfg = normalizeMergeRule(rule);
          const params = cfg.params || {};
          const key = mergeRuleKey(cfg, index);
          const collapsed = !store.state.expandedMergeRules.has(key);
          return `
            <article class="channel-card merge-card ${collapsed ? "collapsed" : ""}" data-merge-index="${index}" data-merge-key="${escapeHtml(key)}">
              <button class="channel-summary" type="button" data-toggle-merge="${index}">
                <div>
                  <div class="channel-title-text">${escapeHtml(mergeRuleTitle(cfg))}</div>
                  <div class="channel-meta">
                    <span>后续 ${escapeHtml(params.next_count ?? params.merge_count ?? 2)} 条</span>
                    <span>超时 ${escapeHtml(params.time_window_seconds ?? 60)} 秒</span>
                  </div>
                </div>
                <span class="chevron">⌄</span>
              </button>
              <div class="channel-body">
                <section class="channel-section active">
                  <div class="form-grid">
                    <label class="field">规则名称<input data-merge-field="name" value="${escapeHtml(cfg.name)}" placeholder="例如：主频道预览图合并" /></label>
                    <label class="field">频道用户名<input data-merge-field="channel" value="${escapeHtml(cfg.channel)}" /></label>
                    <label class="field">规则类型
                      <select data-merge-field="rule_class">
                        ${MERGE_RULE_CLASSES.map((item) => `<option value="${escapeHtml(item.value)}" ${cfg.rule_class === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
                      </select>
                    </label>
                  </div>
                  <div class="form-grid">
                    <label class="field">触发关键词<textarea data-merge-param="trigger_keywords" rows="4">${escapeHtml(joinList(params.trigger_keywords || params.keywords || []))}</textarea></label>
                    <label class="field">触发正则<input data-merge-param="trigger_regex" value="${escapeHtml(params.trigger_regex || "")}" /></label>
                    <label class="field">后续消息数<input data-merge-param="next_count" type="number" min="1" value="${escapeHtml(params.next_count ?? params.merge_count ?? 2)}" /></label>
                    <label class="field">合并超时(秒)<input data-merge-param="time_window_seconds" type="number" min="1" value="${escapeHtml(params.time_window_seconds ?? 60)}" /></label>
                  </div>
                  <p class="field-hint">命中过滤词时，整个合并组会一起过滤；超时未凑够后续消息时，会按已经抓到的消息继续处理。</p>
                </section>
                <div class="channel-actions">
                  <button class="btn btn-soft danger" data-remove-merge="${index}" type="button">删除规则</button>
                </div>
              </div>
            </article>
          `;
        })
        .join("")
    : '<div class="queue-item"><span>暂无合并规则</span><strong>0</strong></div>';

  document.querySelectorAll("[data-toggle-merge]").forEach((button) => {
    button.addEventListener("click", () => {
      collectMergeRules();
      const card = button.closest(".merge-card");
      const key = card.dataset.mergeKey;
      if (store.state.expandedMergeRules.has(key)) {
        store.state.expandedMergeRules.delete(key);
      } else {
        store.state.expandedMergeRules.add(key);
      }
      renderMergeRules();
    });
  });
  document.querySelectorAll("[data-remove-merge]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const index = Number.parseInt(button.dataset.removeMerge, 10);
      collectMergeRules();
      store.state.config.merge_rules.splice(index, 1);
      renderMergeRules();
    });
  });
}

export function initChannels() {
  if (els.addChannelBtn) {
    els.addChannelBtn.addEventListener("click", () => {
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
  if (els.addMergeRuleBtn) {
    els.addMergeRuleBtn.addEventListener("click", () => {
      collectMergeRules();
      if (!Array.isArray(store.state.config.merge_rules)) {
        store.state.config.merge_rules = [];
      }
      const rule = defaultMergeRule();
      store.state.config.merge_rules.push(rule);
      store.state.expandedMergeRules.add(mergeRuleKey(rule, store.state.config.merge_rules.length - 1));
      renderMergeRules();
    });
  }
}
